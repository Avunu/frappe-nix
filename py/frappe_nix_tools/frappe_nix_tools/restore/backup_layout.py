"""frappe-nix's backup layout: one folder per backup, named for its timestamp.

The parser python-devops-nix's backup fetch layer (lib.backups.fetch) is given
for frappe-nix: it turns a recursive object listing into backup sets. The
base's tests/fixtures/backup-layouts/timestamp_folders.py is its reference copy.

From Frappe's S3 backup integration (s3_backup_settings.py):

    folder = doc.backup_path + os.path.basename(db_filename)[:15] + "/"

[:15] is exactly "YYYYMMDD_HHMMSS", so the names sort chronologically. Inside,
from frappe/utils/backups.py set_backup_file_name():

    {ts}-{slug}[-partial]-database[-enc].sql.gz
    {ts}-{slug}-site_config_backup[-enc].json
    {ts}-{slug}-files[-enc].{tar,tgz}
    {ts}-{slug}-private-files[-enc].{tar,tgz}

Two things about those names are load-bearing and easy to get wrong:

  * "-enc" is a naming convention only. The restore decides whether a dump is
    encrypted by running `file` on it. The suffix is surfaced so the caller can
    resolve a key up front, not treated as authoritative.
  * Backup encryption covers only the database and the two archives, never the
    site-config JSON, which carries both encryption keys. A restore is
    therefore self-describing, which is why the site config is always fetched.

Standard library only, and nothing imported from the base or from the rest of
this package: the fetch command copies this one file next to its own module
(as backup_layout.py) and imports it there. parse() reads the listing's entries
by attribute (key, size, is_dir) and returns plain mappings.
"""

import re
from datetime import datetime

ROLES = ("database", "site_config", "files", "private_files")
OPTIONAL_ROLES = ("files", "private_files")
HINT = """\
Frappe's S3 backup integration writes one folder per backup, named for its
timestamp (YYYYMMDD_HHMMSS). Nothing there matched."""

FOLDER = re.compile(r"\d{8}_\d{6}")
DATABASE = re.compile(r"-database(-enc)?\.sql\.gz$")
PARTIAL = re.compile(r"-partial-database(-enc)?\.sql\.gz$")
SITE_CONFIG = re.compile(r"-site_config_backup(-enc)?\.json$")
# "-files.tar" is a suffix of "-private-files.tar", so the public archive has
# to exclude the private one explicitly or it matches both.
FILES = re.compile(r"-files(-enc)?\.(tar|tgz)$")
PRIVATE_FILES = re.compile(r"-private-files(-enc)?\.(tar|tgz)$")


def _first(entries, pattern, exclude=None):
	for e in entries:
		name = e.key.rsplit("/", 1)[-1]
		if pattern.search(name) and not (exclude and exclude.search(name)):
			return e
	return None


def parse(listing):
	folders = {}
	for e in listing:
		head, _, rest = e.key.partition("/")
		if not FOLDER.fullmatch(head):
			continue
		files = folders.setdefault(head, [])
		if rest and "/" not in rest and not e.is_dir:
			files.append(e)

	for name, files in sorted(folders.items()):
		try:
			at = datetime.strptime(name, "%Y%m%d_%H%M%S")
		except ValueError:
			continue
		files.sort(key=lambda e: e.key)
		# A partial dump cannot create a site, and Frappe's restore refuses
		# it, so it must never be selected as "the database". Refused here,
		# before the download, rather than after it.
		database = _first(files, DATABASE, exclude=PARTIAL)
		problem = None
		if database is None:
			partial = _first(files, PARTIAL)
			if partial is not None:
				problem = "\n".join(
					[
						f"{name}/ holds only a PARTIAL backup ({partial.key.rsplit('/', 1)[-1]}).",
						"",
						"A partial dump cannot create a site; frappe's restore_backup() refuses it.",
						"Pick a full backup:  bench restore --list",
						"or apply this one to an existing site with `bench partial-restore`.",
					]
				)
			else:
				problem = "\n".join(
					[f"no database dump in {name}/", "", "The folder contains:"]
					+ [f"  {e.key.rsplit('/', 1)[-1]}" for e in files]
				)
		objects = {
			role: entry
			for role, entry in (
				("database", database),
				("site_config", _first(files, SITE_CONFIG)),
				("files", _first(files, FILES, exclude=PRIVATE_FILES)),
				("private_files", _first(files, PRIVATE_FILES)),
			)
			if entry is not None
		}
		meta = {"folder": name}
		if database is not None:
			db = database.key.rsplit("/", 1)[-1]
			# The production site's slug, recovered from the filename. It is
			# routinely not the development site's name, and the caller needs
			# to know that to warn about it.
			meta["slug"] = DATABASE.sub("", db[len(name) + 1 :] if db.startswith(name + "-") else db)
			meta["encrypted"] = db.endswith("-enc.sql.gz")
		yield {
			"name": name,
			"at": at,
			"objects": objects,
			"meta": meta,
			"problem": problem,
		}
