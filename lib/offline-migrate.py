#!/usr/bin/env python3
"""Apply a migrate's schema changes to large tables without locking them.

``bench migrate`` alters a table with a plain ``ALTER TABLE``. On a table of
millions of rows that copy runs for minutes, takes a metadata lock on the table
for the duration of its start and end, and queues every other connection that
touches the table behind it — which is how a migrate on a busy site ends in
``Lock wait timeout exceeded``, either its own or someone else's.

This runs *before* ``bench migrate``. It works out which tables the migrate is
about to ALTER, and for each one of at least ``--threshold`` rows it applies the
change with ``pt-online-schema-change`` instead: a shadow copy of the table is
altered and filled in chunks while triggers keep it current, then swapped in
with one rename. Nothing waits. By the time ``bench migrate`` runs, those
columns already exist, so its own sync finds nothing to alter on those tables.

What it covers is what ``bench migrate`` itself derives a table's schema from:
the DocType JSON an installed app ships, and the Custom Field JSON an app syncs
on migrate. What it does not is anything that has to touch the rows
first — a change to NOT NULL needs its existing NULLs backfilled, and that
UPDATE is the migrate's to run, so the table is left to it and reported.

It does not run the migrate's patches. A pre-model-sync patch that renames a
column in a table this has already widened — ``rename_field`` onto a name this
added — fails on the duplicate; ``FRAPPE_OFFLINE_MIGRATE=0`` runs that one
migrate the stock way.

Run by the interpreter of the bench's environment. Exit 1 if a table could not
be altered, so that the migrate behind it does not run on a half-applied plan.

  --plan      say what would be done and change nothing
  --dry-run   have pt-online-schema-change rehearse it (creates and drops the
              shadow table; copies no rows)
"""

import argparse
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field

#: Set to 0/false/no/off to skip this step for one command.
ENV_ENABLED = "FRAPPE_OFFLINE_MIGRATE"
#: Rows at which a table is altered online.
ENV_THRESHOLD = "FRAPPE_OFFLINE_MIGRATE_ROW_THRESHOLD"
#: Absolute path of pt-online-schema-change, where it is not on PATH.
ENV_PT_OSC = "FRAPPE_OFFLINE_MIGRATE_PT_OSC"

DEFAULT_THRESHOLD = 100_000
PT_OSC = "pt-online-schema-change"
OFF = {"0", "false", "no", "off"}

# What a plan may run against the database. Anything else is a write that only
# the migrate itself should make.
READ_PREFIXES = ("select", "show", "describe", "desc ", "explain", "with")


class WriteAttempt(Exception):
    """Planning reached a statement that writes rows."""


@dataclass
class TablePlan:
    doctype: str
    rows: int
    clauses: list = field(default_factory=list)
    #: Why the table is left to bench migrate, if it is.
    left: str = ""


# ── pure helpers ─────────────────────────────────────────────────────────────


def looks_like_write(query):
    head = str(query).lstrip().lstrip("(").lstrip().lower()
    return not head.startswith(READ_PREFIXES)


def parse_threshold(value):
    """A row count from a flag or variable; None when absent."""
    if value is None or str(value).strip() == "":
        return None
    try:
        rows = int(str(value).replace(",", "").replace("_", "").strip())
    except ValueError:
        raise SystemExit(f"offline-migrate: row threshold {value!r} is not a number")
    if rows < 0:
        raise SystemExit(f"offline-migrate: row threshold {value!r} is negative")
    return rows


def clause_of(query, table):
    """``ADD COLUMN …`` out of ``ALTER TABLE `tab` ADD COLUMN …``; None if it is not that."""
    prefix = f"ALTER TABLE `{table}` "
    return query[len(prefix) :] if query.startswith(prefix) else None


def combine(clauses):
    """One ALTER body for pt-online-schema-change, which takes a single one."""
    return ", ".join(dict.fromkeys(c for c in clauses if c))


def option_file(user, password, host=None, port=None, socket=None):
    """A client option file, so no credential is ever on a command line."""

    def quote(value):
        return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'

    lines = ["[client]", f"user={quote(user)}"]
    if password:
        lines.append(f"password={quote(password)}")
    if socket:
        lines.append(f"socket={quote(socket)}")
    else:
        lines.append(f"host={quote(host or '127.0.0.1')}")
        if port:
            lines.append(f"port={int(port)}")
    return "\n".join(lines) + "\n"


def build_command(
    pt_osc, database, table, alter, defaults_file, dry_run=False, extra=()
):
    """The pt-online-schema-change invocation. ``extra`` goes last, so it wins."""
    return [
        pt_osc,
        f"--alter={alter}",
        f"--defaults-file={defaults_file}",
        f"D={database},t={table}",
        # A site's database user owns its database and nothing else, so it may
        # not ask the server about replication (SHOW SLAVE STATUS wants a global
        # privilege). Two things would: looking for replicas to watch the lag of,
        # which frappe-nix, running one node, has none of — a bench that
        # replicates passes --recursion-method=processlist (or hosts) after
        # `--`; and the refusal to run on a replica of a row-based source, which
        # --force waives. Its other two meanings (foreign keys, --where) are not
        # in play: --alter-foreign-keys-method is not none, and --where is unused.
        "--recursion-method=none",
        "--force",
        "--no-check-replication-filters",
        "--alter-foreign-keys-method=auto",
        "--no-check-alter",
        "--progress=percentage,1",
        "--chunk-size=1000",
        "--max-load=Threads_running=50",
        "--critical-load=Threads_running=100",
        *extra,
        "--dry-run" if dry_run else "--execute",
    ]


def say(message=""):
    print(message, flush=True)


# ── what the next sync would change ──────────────────────────────────────────


def doctype_files():
    """DocType name -> path of its JSON, for every installed app."""
    import frappe
    from frappe.model.sync import get_doc_files
    from frappe.modules.import_file import read_doc_from_file

    found = {}
    for app in frappe.get_installed_apps():
        files = []
        for module in frappe.local.app_modules.get(app) or []:
            folder = os.path.dirname(frappe.get_module(f"{app}.{module}").__file__)
            files = get_doc_files(files=files, start_path=folder)

        for path in files:
            try:
                docs = read_doc_from_file(path)
            except OSError:
                continue
            for doc in docs if isinstance(docs, list) else [docs]:
                if doc and doc.get("doctype") == "DocType":
                    found[doc["name"]] = path
    return found


def pending_doctypes():
    """The DocTypes whose JSON the next sync imports, by the test Frappe's own
    ``import_file_by_path`` applies: a stored hash that no longer matches. One
    not yet installed has no table to alter; its sync creates it empty."""
    import frappe
    from frappe.modules.import_file import calculate_hash

    hashed = frappe.db.has_column("DocType", "migration_hash")
    pending = {}
    for name, path in doctype_files().items():
        if not frappe.db.get_value("DocType", name, "modified"):
            continue
        stored = (
            frappe.db.get_value("DocType", name, "migration_hash") if hashed else None
        )
        if stored and stored == calculate_hash(path):
            continue
        pending[name] = path
    return pending


def synced_custom_fields():
    """doctype -> {fieldname: field} for the Custom Fields apps sync on migrate,
    and the subset of those doctypes where a field is new or newer than the DB's."""
    import frappe
    from frappe.utils import get_datetime

    fields, changed = {}, set()
    for app in frappe.get_installed_apps():
        for module in frappe.local.app_modules.get(app) or []:
            folder = frappe.get_app_path(app, module, "custom")
            if not os.path.isdir(folder):
                continue
            for name in os.listdir(folder):
                if not name.endswith(".json"):
                    continue
                with open(os.path.join(folder, name)) as f:
                    data = json.load(f)
                if not data.get("sync_on_migrate") or not frappe.db.exists(
                    "DocType", data["doctype"]
                ):
                    continue
                for custom in data.get("custom_fields") or []:
                    dt = custom.get("dt")
                    fields.setdefault(dt, {}).setdefault(custom["fieldname"], custom)
                    existing = frappe.db.get_value(
                        "Custom Field",
                        {"dt": dt, "fieldname": custom["fieldname"]},
                        "modified",
                    )
                    shipped = custom.get("modified")
                    if not existing or (shipped and existing < get_datetime(shipped)):
                        changed.add(dt)
    return fields, changed


def setup_custom_fields(doctype):
    """Fields an app's ``setup.get_custom_fields()`` declares for the doctype."""
    import frappe

    found = {}
    for app in frappe.get_installed_apps():
        try:
            declared = frappe.get_module(f"{app}.setup").get_custom_fields()
        except ImportError:
            continue
        except AttributeError:
            continue
        except Exception as e:
            say(
                f"offline-migrate: {app}.setup.get_custom_fields() failed ({e}); its fields are not planned"
            )
            continue
        if not isinstance(declared, dict):
            continue
        for custom in declared.get(doctype, []):
            if isinstance(custom, dict) and custom.get("fieldname"):
                found.setdefault(custom["fieldname"], {**custom, "dt": doctype})
    return found


@contextlib.contextmanager
def recording(statements):
    """Run Frappe's schema code with its DDL recorded rather than executed, and
    refuse any other write. ``sql_ddl`` is what ``MariaDBTable.alter`` ends in;
    but ``alter`` also backfills NULLs before a NOT NULL change with a real
    UPDATE, which on the table this exists for would be the very lock it avoids."""
    import frappe

    db = frappe.local.db
    saved = {name: db.__dict__.get(name) for name in ("sql", "sql_ddl")}
    real_sql = db.sql

    def sql(query, *args, **kwargs):
        if looks_like_write(query):
            raise WriteAttempt(str(query))
        return real_sql(query, *args, **kwargs)

    db.sql = sql
    db.sql_ddl = lambda query, *args, **kwargs: statements.append(str(query))
    try:
        yield
    finally:
        for name, value in saved.items():
            if value is None:
                db.__dict__.pop(name, None)
            else:
                setattr(db, name, value)


def alter_clauses(doctype, path, custom):
    """The ALTER clauses ``bench migrate`` would run on the doctype's table, or
    ``([], reason)`` when it cannot be said. ``custom`` is fieldname -> field."""
    import frappe
    from frappe.database.mariadb.schema import MariaDBTable
    from frappe.model.meta import Meta

    if path:
        with open(path) as f:
            meta = Meta(frappe.get_doc(json.load(f)))
    else:
        meta = Meta(doctype)

    if meta.get("issingle") or meta.get("is_virtual"):
        return [], ""

    # The meta the sync will leave behind: the doctype as shipped, plus the
    # custom fields that arrive with it.
    have = {df.fieldname: df for df in meta.fields}
    for fieldname, custom_field in custom.items():
        if fieldname in have:
            have[fieldname].update(custom_field)
        else:
            meta.fields.append(frappe._dict(custom_field))

    table = MariaDBTable(doctype, meta)
    if table.is_new():
        return [], ""

    queries, out = [], io.StringIO()
    try:
        with recording(queries), contextlib.redirect_stdout(out):
            table.validate()
            table.alter()
    except WriteAttempt:
        return [], "the change backfills existing rows before it can be applied"
    except frappe.ValidationError as e:
        return [], f"Frappe refuses it: {e}"
    except Exception:
        sys.stdout.write(out.getvalue())
        raise

    clauses = [clause_of(q, table.table_name) for q in queries]
    if None in clauses:
        return [], "its ALTER is not in a form pt-online-schema-change takes"
    return clauses, ""


def large_tables(threshold):
    """TablePlans for every table of ``threshold`` rows or more that the next
    migrate would alter."""
    import frappe

    frappe.db.get_tables(cached=False)  # is_new() reads a cache a restore leaves stale

    pending = pending_doctypes()
    synced, changed = synced_custom_fields()
    plans = []

    for doctype in sorted(set(pending) | changed):
        custom = dict(synced.get(doctype, {}))
        if doctype in changed:
            for fieldname, custom_field in setup_custom_fields(doctype).items():
                custom.setdefault(fieldname, custom_field)

        try:
            clauses, left = alter_clauses(doctype, pending.get(doctype), custom)
        except Exception as e:
            say(f"offline-migrate: could not plan {doctype}: {type(e).__name__}: {e}")
            continue
        if not clauses and not left:
            continue

        rows = frappe.db.count(doctype, distinct=False)
        if rows >= threshold:
            plans.append(TablePlan(doctype, rows, clauses, left))
    return plans


# ── doing it ─────────────────────────────────────────────────────────────────


def find_pt_osc():
    return os.environ.get(ENV_PT_OSC) or shutil.which(PT_OSC)


def run_online(plan, pt_osc, db, dry_run, extra):
    """pt-online-schema-change for one table. Output streams as it is produced:
    a copy that takes an hour must not look like a hang."""
    alter = combine(plan.clauses)
    table = f"tab{plan.doctype}"

    handle = tempfile.NamedTemporaryFile(
        "w", prefix="offline-migrate-", suffix=".cnf", delete=False
    )
    try:
        with handle:  # created 0600
            handle.write(option_file(db.user, db.password, db.host, db.port, db.socket))
        cmd = build_command(
            pt_osc, db.cur_db_name, table, alter, handle.name, dry_run, extra
        )

        say(f"\noffline-migrate: {plan.doctype} ({plan.rows:,} rows)")
        say(f"  ALTER TABLE `{table}` {alter}")
        return subprocess.run(cmd).returncode == 0
    finally:
        with contextlib.suppress(OSError):
            os.unlink(handle.name)


def resolve_threshold(flag):
    """The flag, else the environment (which the bench scripts default from the
    Nix option), else the built-in default."""
    for value in (flag, os.environ.get(ENV_THRESHOLD)):
        rows = parse_threshold(value)
        if rows is not None:
            return rows
    return DEFAULT_THRESHOLD


def parse_args(argv):
    p = argparse.ArgumentParser(
        prog="offline-migrate",
        description="Alter large tables online before bench migrate. Arguments after -- go to pt-online-schema-change.",
    )
    p.add_argument("--site", default=os.environ.get("FRAPPE_SITE"))
    p.add_argument(
        "--bench-root", default=os.environ.get("FRAPPE_BENCH_ROOT") or os.getcwd()
    )
    p.add_argument(
        "--threshold",
        help=f"rows at which a table is altered online (default {DEFAULT_THRESHOLD:,})",
    )
    mode = p.add_mutually_exclusive_group()
    mode.add_argument(
        "--plan", action="store_true", help="report what would be done; change nothing"
    )
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="rehearse with pt-online-schema-change --dry-run",
    )
    p.add_argument("extra", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)
    args = p.parse_args(argv)
    args.extra = [a for a in args.extra if a != "--"]
    if not args.site:
        p.error("no site: pass --site or set FRAPPE_SITE")
    return args


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)

    if os.environ.get(ENV_ENABLED, "").strip().lower() in OFF:
        say(
            f"offline-migrate: skipped ({ENV_ENABLED}={os.environ[ENV_ENABLED]}); bench migrate alters every table itself"
        )
        return 0

    import frappe

    os.chdir(os.path.join(args.bench_root, "sites"))
    frappe.init(args.site)
    frappe.connect()
    try:
        if frappe.db.db_type != "mariadb":
            say(
                f"offline-migrate: {frappe.db.db_type} is not supported; bench migrate alters every table itself"
            )
            return 0

        threshold = resolve_threshold(args.threshold)
        say(
            f"offline-migrate: {args.site}: tables of {threshold:,}+ rows with schema changes pending…"
        )
        plans = large_tables(threshold)

        # The planning read a snapshot and holds a metadata lock on every table
        # it touched until the transaction ends; pt-online-schema-change's final
        # rename needs the exclusive one.
        frappe.db.rollback()

        online = [p for p in plans if p.clauses]
        for plan in plans:
            if not plan.clauses:
                say(
                    f"offline-migrate: {plan.doctype} ({plan.rows:,} rows) is left to bench migrate: {plan.left}"
                )
        if not online:
            say("offline-migrate: nothing to alter online")
            return 0
        if args.plan:
            for plan in online:
                say(
                    f"offline-migrate: {plan.doctype} ({plan.rows:,} rows)\n  ALTER TABLE `tab{plan.doctype}` {combine(plan.clauses)}"
                )
            return 0

        pt_osc = find_pt_osc()
        if not pt_osc:
            say(
                f"offline-migrate: {PT_OSC} (percona-toolkit) is not installed, and {len(online)} table(s) need it"
            )
            return 1

        for plan in online:
            if not run_online(plan, pt_osc, frappe.local.db, args.dry_run, args.extra):
                say(
                    f"\noffline-migrate: {plan.doctype} FAILED; stopping before bench migrate"
                )
                return 1
            say(f"offline-migrate: {plan.doctype} done")

        if not args.dry_run:
            frappe.clear_cache()
        return 0
    finally:
        frappe.destroy()


if __name__ == "__main__":
    sys.exit(main())
