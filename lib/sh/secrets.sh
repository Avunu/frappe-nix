# Secrets-related reconciliation for the scaffolder/migrator.
#
# Nothing here creates or encrypts anything: frappe-init cannot know the
# recipients, and inventing an empty rules file would be worse than leaving the
# bench alone. What it does is make the two ways a bench gets this wrong
# visible — a committed site_config.json, and an .age file the build cannot see.

# A classic bench keeps its credentials in sites/<site>/site_config.json, and
# because nothing ever told it not to, that file is usually committed. It holds
# the Frappe encryption key, the database password and whatever object-storage
# keys the site uses.
#
# The managed .gitignore block already excludes the path, so a *new* bench is
# fine; this is about the ones that tracked it before the block existed. Git
# keeps honouring an entry already in the index no matter what .gitignore says,
# so the exclusion alone changes nothing for them.
#
# Reported rather than fixed: `git rm --cached` is a staged deletion of a file
# the bench is actively using, and the migrator's contract is that it never
# deletes. The rotation it implies is a human decision too — the values are in
# the repository's history, not just its worktree, so untracking the file
# protects the next commit and nothing before it.
audit_tracked_site_configs() {
  local f found=0
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    found=1
    warn "$f is tracked by git.

     That file normally holds this site's Frappe encryption_key, db_password and
     any object-storage credentials. frappe-nix's .gitignore block excludes the
     path, but git keeps honouring an entry that is already in the index, so
     nothing changes until you untrack it:

         git rm --cached '$f'

     Treat every credential it has ever held as disclosed and rotate it — the
     values are in the repository's history, not only in the worktree."
  done < <(git ls-files 'sites/*/site_config.json' 2>/dev/null || true)
  # Never fatal: an existing bench with a tracked site_config is exactly the
  # case the migrator exists to meet halfway, and refusing to run would leave
  # it with neither the .gitignore block nor the warning.
  [ "$found" = 0 ] || true
}

# The mirror image: age ciphertext is *meant* to be committed. A flake's source
# tree is exactly its git-tracked files, so an untracked or ignored .age file is
# invisible to the build — agenix-shell reports it missing at shell entry, which
# reads as "my key is wrong" rather than "the file was never added".
verify_secrets_visible() {
  local p bad=0 any=0
  while IFS= read -r p; do
    [ -n "$p" ] || continue
    any=1
    if git check-ignore -q -- "$p" 2>/dev/null; then
      err "$p is excluded by .gitignore"
      bad=1
    elif ! git ls-files --error-unmatch -- "$p" >/dev/null 2>&1; then
      warn "$p is not tracked by git — the flake cannot see it. Run: git add '$p'"
    fi
  done < <(find secrets -name '*.age' -type f 2>/dev/null | sort || true)

  [ "$any" = 1 ] || return 0
  [ "$bad" = 0 ] ||
    die "the bench .gitignore excludes age secrets (they are ciphertext and belong in the repo)"
}

# `secrets/` on a fresh scaffold, so the layout is discoverable before anyone
# has a key to put in it. Only on init: an existing bench that keeps its secrets
# elsewhere should not grow an empty directory it did not ask for.
scaffold_secrets_dir() {
  [ -d secrets ] && return 0
  mkdir -p secrets
  cat >secrets/README.md <<'EOF'
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
EOF
}
