# Checks for `frappe-init`: migration, preset resolution, and its modes, flags
# and exit codes.
#
# Plain runCommand rather than runNixOSTest: both are offline (the fixture's
# "remotes" are local bare repos, and the migrator runs with --skip-lock), so
# they also run on darwin and are cheap enough for the PR gate.
{ pkgs, frappe-init }:

let
  deps = with pkgs; [
    frappe-init
    git
    jq
    python3
    diffutils
    findutils
    coreutils
  ];
in
{
  # Full end-to-end migration of a synthetic classic bench: submodule pinning,
  # vendoring, config reconciliation, ignore rules, idempotency, guard rails.
  migrate-classic =
    pkgs.runCommand "frappe-nix-migrate-classic-check"
      {
        nativeBuildInputs = deps;
      }
      ''
        bash ${./migrate-classic.sh} \
          ${frappe-init}/bin/frappe-init \
          ${./make-classic-bench.sh} 2>&1 | tee "$out"
      '';

  # Which preset an existing bench resolves to, and how the unsupported and
  # undetectable cases fail.
  migrate-versions =
    pkgs.runCommand "frappe-nix-migrate-versions-check"
      {
        nativeBuildInputs = deps;
      }
      ''
        bash ${./migrate-versions.sh} ${frappe-init}/bin/frappe-init 2>&1 | tee "$out"
      '';

  # The modes, flags and exit codes the two above leave alone: scaffolding a
  # bench (plan, seeds, next steps), the mode guards and every documented exit
  # code, and the migration flags.
  scaffold-modes =
    pkgs.runCommand "frappe-nix-scaffold-modes-check"
      {
        nativeBuildInputs = deps;
      }
      ''
        bash ${./scaffold-modes.sh} \
          ${frappe-init}/bin/frappe-init \
          ${./make-classic-bench.sh} 2>&1 | tee "$out"
      '';
}
