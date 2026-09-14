# Keeps a bench's workspace root — pyproject.toml, and uv.lock when the root
# moved — in step with what this frappe-nix expects of one. Run from the dev
# shell's enterShell, so a frappe-nix bump that asks something new of a bench
# is answered on the next shell entry rather than by a command run from outside
# it.
#
# The case it was written for: frappe-runtime became a required workspace
# dependency — it is the process the dev shell runs, and uv2nix can only build
# what uv.lock names. A bench from before that has no way to know, and the check
# that noticed lived at *evaluation*: `nix develop` refused to open, and the fix
# it named (`nix run github:Avunu/frappe-nix -- -y`) had to be run from outside
# the shell it blocked. Evaluation now degrades instead (modules/devenv.nix runs
# the split processes for that one session) and this does the reconciling.
#
# It is `frappe-nix-workspace ensure-root` — the same call `frappe-init` makes,
# which adds only what is missing and changes nothing already correct — followed
# by `uv lock` when, and only when, that changed the file. Not the rest of the
# reconciler: classifying apps, registering submodules and `git add -A` are a
# migration's business, not a shell hook's.
#
# A lock that fails restores both files. The alternative — a pyproject.toml
# declaring what uv.lock does not carry — fails the *next* evaluation (see
# lib/lock-audit.nix) and is back to needing `nix run .#relock` from outside,
# which is exactly the shape this exists to remove. The shell stays openable, the
# hook says why it could not finish, and the next entry tries again.
#
# uv is taken from PATH on purpose: the dev shell carries its own, and the lock
# should be written by that one, not by a second uv baked in here.
{ pkgs }:

let
  workspaceTool = import ./workspace-tool.nix { inherit pkgs; };
in
pkgs.writeShellApplication {
  name = "frappe-nix-root-sync";
  runtimeInputs = with pkgs; [
    coreutils
    diffutils
    workspaceTool
  ];
  text = ''
    if [ "$#" -ne 1 ]; then
      echo "usage: frappe-nix-root-sync <bench-root>" >&2
      exit 2
    fi
    cd "$1"
    if [ ! -f pyproject.toml ]; then
      echo "frappe-nix-root-sync: no pyproject.toml in $PWD" >&2
      exit 2
    fi

    # Copies to fall back to. -p keeps the mtimes, so a restored file is the
    # file it was, to git and to anything that stamps on mtime.
    keep="$(mktemp -d)"
    cp -p pyproject.toml "$keep/pyproject.toml"
    [ ! -f uv.lock ] || cp -p uv.lock "$keep/uv.lock"

    restore() {
      cp -p "$keep/pyproject.toml" pyproject.toml
      if [ -f "$keep/uv.lock" ]; then
        cp -p "$keep/uv.lock" uv.lock
      else
        rm -f uv.lock
      fi
    }

    # Anything but a completed run — a failed lock, or a ^C in the middle of
    # one — puts the files back. Interrupting `uv lock` matters: it writes the
    # lock last, so a pyproject.toml already reconciled next to the old lock is
    # the exact half-state the audit refuses to evaluate.
    completed=false
    finish() {
      $completed || restore
      rm -rf "$keep"
    }
    trap finish EXIT
    trap 'exit 130' INT TERM

    # The template unrendered: its tokens are all inside strings, so it parses,
    # and ensure-root reads only the token-free tables from it — the
    # extra-build-dependencies and the non-app [tool.uv.sources].
    if ! frappe-nix-workspace ensure-root \
      --pyproject pyproject.toml \
      --template ${../templates/bench/pyproject.toml}; then
      echo "frappe-nix-root-sync: could not reconcile pyproject.toml — left as it was" >&2
      exit 1
    fi

    if cmp -s pyproject.toml "$keep/pyproject.toml"; then
      completed=true
      exit 0
    fi

    echo "pyproject.toml reconciled with frappe-nix (see above) — re-locking the Python workspace…"
    if ! command -v uv > /dev/null 2>&1; then
      echo "frappe-nix-root-sync: uv is not on PATH; pyproject.toml restored" >&2
      exit 1
    fi
    if ! uv lock; then
      echo "" >&2
      echo "frappe-nix-root-sync: uv lock failed — pyproject.toml and uv.lock restored, so the" >&2
      echo "  shell keeps opening. Re-entering the shell retries. To resolve it by hand, make the" >&2
      echo "  additions listed above in pyproject.toml and run 'uv lock' here (uv is on PATH)." >&2
      exit 1
    fi
    completed=true

    echo ""
    echo "frappe-nix: pyproject.toml and uv.lock updated — commit them, then re-enter the shell"
    echo "  (direnv reload, or exit and nix develop): this one was built from the previous uv.lock."
  '';
}
