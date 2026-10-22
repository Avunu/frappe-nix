# Checks for `frappe-init`: migration, preset resolution, and its modes, flags
# and exit codes.
#
# Script checks rather than VM tests: both are offline (the fixture's
# "remotes" are local bare repos, and the migrator runs with --skip-lock), so
# they also run on darwin and are cheap enough for the PR gate.
{
  pkgs,
  checks,
  frappe-init,
}:

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
  migrate-classic = checks.script "migrate-classic" { tools = deps; } ''
    bash ${./migrate-classic.sh} \
      ${frappe-init}/bin/frappe-init \
      ${./make-classic-bench.sh} 2>&1 | tee "$out"
  '';

  # Which preset an existing bench resolves to, and how the unsupported and
  # undetectable cases fail.
  migrate-versions = checks.script "migrate-versions" { tools = deps; } ''
    bash ${./migrate-versions.sh} ${frappe-init}/bin/frappe-init 2>&1 | tee "$out"
  '';

  # The modes, flags and exit codes the two above leave alone: scaffolding a
  # bench (plan, seeds, next steps), the mode guards and every documented exit
  # code, and the migration flags.
  scaffold-modes = checks.script "scaffold-modes" { tools = deps; } ''
    bash ${./scaffold-modes.sh} \
      ${frappe-init}/bin/frappe-init \
      ${./make-classic-bench.sh} 2>&1 | tee "$out"
  '';
}
