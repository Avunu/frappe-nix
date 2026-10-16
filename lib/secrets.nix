# The build-time half of the secrets toolkit for a frappe-nix bench:
# python-devops-nix's lib.secrets.tools (agecheck, the generated agenix rules,
# edit-secret, rekey-secrets, check-secrets, setup-backup-access and the
# on-demand decryption snippets), with frappe-nix's names.
#
# `cfg` is the evaluated `frappe-nix.secrets` (modules/secrets.nix), which is
# top-level, where `pkgs` does not exist; modules/devenv.nix calls this from
# perSystem.
{
  pkgs,
  python-devops-nix,
  cfg,
}:

python-devops-nix.lib.secrets.tools {
  inherit pkgs cfg;
  label = "frappe-nix";
  optionPath = "frappe-nix.secrets";
  restoreCommand = "bench restore";
  bucketExample = "MyERPBackups";
  # setup-backup-access's check that the credentials reach the backups: count
  # them and name the newest, as frappe-nix always has.
  verifyListing = ''
    local listing
    listing="$("$FETCH" list)" || return
    printf '%s backup(s) found; newest is %s\n' \
      "$(printf '%s\n' "$listing" | grep -c .)" "$(printf '%s\n' "$listing" | tail -n1)"
  '';
  python = pkgs.python314;
}
