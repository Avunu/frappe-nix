# Migration checks for `frappe-init --migrate`.
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
  migrate-classic = pkgs.runCommand "frappe-nix-migrate-classic-check" {
    nativeBuildInputs = deps;
  } ''
    bash ${./migrate-classic.sh} \
      ${frappe-init}/bin/frappe-init \
      ${./make-classic-bench.sh} 2>&1 | tee "$out"
  '';

  # Which preset an existing bench resolves to, and how the unsupported and
  # undetectable cases fail.
  migrate-versions = pkgs.runCommand "frappe-nix-migrate-versions-check" {
    nativeBuildInputs = deps;
  } ''
    bash ${./migrate-versions.sh} ${frappe-init}/bin/frappe-init 2>&1 | tee "$out"
  '';
}
