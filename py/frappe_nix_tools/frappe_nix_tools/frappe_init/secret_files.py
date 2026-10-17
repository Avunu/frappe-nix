"""Secrets, as far as the scaffolder goes.

Nothing here creates or encrypts anything: frappe-init cannot know the
recipients, and an empty rules file would be worse than none. It makes the two
ways a bench gets this wrong visible (a committed site_config.json, an .age file
the build cannot see), and seeds secrets/README.md in a new bench.
"""

import os
from pathlib import Path

from frappe_nix_tools.frappe_init import ui
from frappe_nix_tools.frappe_init.proc import git, probe, probe_ok
from frappe_nix_tools.frappe_init.state import Run

SECRETS_README = """\
# secrets/

age-encrypted credentials for this bench. **These files are committed** — they
are ciphertext, and a flake's source tree is exactly its git-tracked files, so
an untracked one is invisible to the build.

Declare who may read them in `flake.nix`:

    frappe-nix.secrets = {
      dir = ./secrets;
      recipients.you = "ssh-ed25519 AAAA… you@host";
      sites."<your-site>" = { };
    };

Then, in the dev shell:

    edit-secret backup-access    # object-store credentials for `bench restore`
    check-secrets                # are the .age files encrypted to who we think?
    rekey-secrets                # after changing `recipients`

There is deliberately no `secrets.nix`: the recipient list in `flake.nix` is the
only place it is written down, and the rules file agenix reads is generated from
it. A hand-maintained rules file can be edited without re-encrypting anything,
and nothing notices.
"""


def audit_tracked_site_configs(st: Run) -> None:
	"""A classic bench keeps its credentials in sites/<site>/site_config.json and
	usually commits it. The managed .gitignore block excludes the path, but git
	keeps honouring an entry already in the index, so that alone changes nothing.

	Reported, never fixed and never fatal: `git rm --cached` is a staged deletion
	of a file the bench is using, and the rotation it implies is a human
	decision, since the values are in the history too.
	"""
	tracked = probe(git("ls-files", "sites/*/site_config.json")) or ""
	for f in tracked.splitlines():
		if not f:
			continue
		st.warn(
			f"""{f} is tracked by git.

     That file normally holds this site's Frappe encryption_key, db_password and
     any object-storage credentials. frappe-nix's .gitignore block excludes the
     path, but git keeps honouring an entry that is already in the index, so
     nothing changes until you untrack it:

         git rm --cached '{f}'

     Treat every credential it has ever held as disclosed and rotate it — the
     values are in the repository's history, not only in the worktree."""
		)


def verify_secrets_visible(st: Run) -> None:
	"""age ciphertext is meant to be committed: an untracked or ignored .age file
	is invisible to the build, which reads as "my key is wrong" at shell entry."""
	found: list[str] = []
	for root, _, files in os.walk("secrets"):
		found += [
			os.path.join(root, f)
			for f in files
			if f.endswith(".age") and os.path.isfile(os.path.join(root, f))
		]
	bad = False
	for p in sorted(found):
		if probe_ok(git("check-ignore", "-q", "--", p)):
			ui.err(f"{p} is excluded by .gitignore")
			bad = True
		elif not probe_ok(git("ls-files", "--error-unmatch", "--", p)):
			st.warn(f"{p} is not tracked by git — the flake cannot see it. Run: git add '{p}'")
	if bad:
		ui.die("the bench .gitignore excludes age secrets (they are ciphertext and belong in the repo)")


def scaffold_secrets_dir() -> None:
	"""secrets/ in a new bench, so the layout is discoverable before anyone has a
	key. Only on init: an existing bench that keeps its secrets elsewhere should
	not grow a directory it did not ask for."""
	if os.path.isdir("secrets"):
		return
	Path("secrets").mkdir(parents=True, exist_ok=True)
	Path("secrets/README.md").write_text(SECRETS_README)


def secrets_declared(flake: str) -> bool:
	"""flake.nix declares frappe-nix.secrets: a line that names it outside a comment."""
	return any(
		"frappe-nix.secrets" in line and not line.lstrip().startswith("#") for line in flake.splitlines()
	)
