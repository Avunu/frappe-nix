# The dev shell's reconcile-apps with frappe-nix.renamedApps and replacedApps
# around it (docs/app-standards/spec.md §5.10): rename the site first, so the
# renamed app is not installed beside its old self (they share a Module Def);
# reconcile; then install each replacing app and uninstall the app it replaces.
#
# A function so tests/standards/rename.nix can run it against stubs.
#
#   reconcileExec  lib/scripts.nix's reconcile-apps body (it exits early on its
#                  quiet paths, so it runs as a script of its own)
#   pythonBin, benchBin   the bench's interpreter and bench CLI
#   renamedApps, replacedApps   OLD = NEW
#
# Returns the reconcile-apps body: the original unchanged when both are empty.
{ pkgs, lib }:
{
  reconcileExec,
  pythonBin,
  benchBin,
  renamedApps,
  replacedApps,
}:
let
  pairs = attrs: lib.mapAttrsToList (old: new: "${old}=${new}") attrs;
  base = pkgs.writeShellScript "frappe-nix-reconcile-apps" reconcileExec;
  grep = lib.getExe' pkgs.gnugrep "grep";
  # On the site only, and under reconcile-apps' own lock: every process that
  # waits on frappe:apps-reconcile runs this from its own devenv-tasks wrapper,
  # several copies in the same second (see lib/scripts.nix). The subshell holds
  # the lock from before it looks at the site, so a copy that waited finds the
  # work done; it lets go before reconcile-apps itself, which takes the same
  # lock.
  onSite = body: ''
    if [ -n "$SITE" ] && [ -d "$FRAPPE_BENCH_ROOT/sites/$SITE" ]; then
      mkdir -p "$FRAPPE_BENCH_ROOT/sites/$SITE/locks"
      (
        ${pkgs.util-linux}/bin/flock 8
        ${body}
      ) 8>"$FRAPPE_BENCH_ROOT/sites/$SITE/locks/reconcile-apps.lock"
    fi
  '';
in
if renamedApps == { } && replacedApps == { } then
  reconcileExec
else
  ''
    set -euo pipefail
    export _FRAPPE_BENCH_RAW=1
    SITE="''${1:-''${FRAPPE_SITE:-}}"
    ${lib.optionalString (renamedApps != { }) (onSite ''
      cd "$FRAPPE_BENCH_ROOT/sites"
      ${pythonBin} ${./frappe_rename_app.py} --site "$SITE" --yes ${lib.escapeShellArgs (pairs renamedApps)}
    '')}
    ${lib.optionalString (replacedApps != { }) ''
      # Each OLD is still in sites/apps.txt (it leaves the bench only after
      # every site has run this), so reconcile-apps would install it again on
      # every start, for the step below to uninstall it again.
      export FRAPPE_NIX_RECONCILE_SKIP=${lib.escapeShellArg (lib.concatStringsSep " " (lib.attrNames replacedApps))}
    ''}
    ${base} "$@"
    ${lib.optionalString (replacedApps != { }) (onSite ''
      cd "$FRAPPE_BENCH_ROOT"
      installed="$(${benchBin} --site "$SITE" list-apps --format json 2>/dev/null \
        | ${lib.getExe pkgs.jq} -r --arg s "$SITE" '.[$s][]? // empty' 2>/dev/null || true)"
      for pair in ${lib.escapeShellArgs (pairs replacedApps)}; do
        old="''${pair%%=*}" new="''${pair#*=}"
        ${grep} -qxF "$old" <<< "$installed" || continue
        if ! ${grep} -qxF "$new" <<< "$installed"; then
          echo "reconcile-apps: $new replaces $old on $SITE: installing $new"
          ${benchBin} --site "$SITE" install-app "$new"
        fi
        echo "reconcile-apps: $new replaces $old on $SITE: uninstalling $old"
        ${benchBin} --site "$SITE" uninstall-app "$old" --yes --no-backup
      done
    '')}
  ''
