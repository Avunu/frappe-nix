# The dev shell's reconcile-apps with frappe-nix.renamedApps and replacedApps
# around it (docs/ironclad/spec.md §5.10): rename the site first, so the
# renamed app is not installed beside its old self (they share a Module Def);
# reconcile; then install each replacing app and uninstall the app it replaces.
#
# A function so tests/ironclad/rename.nix can run it against stubs.
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
  onSite = body: ''
    if [ -n "$SITE" ] && [ -d "$FRAPPE_BENCH_ROOT/sites/$SITE" ]; then
      ${body}
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
      (cd "$FRAPPE_BENCH_ROOT/sites" \
        && ${pythonBin} ${./frappe_rename_app.py} --site "$SITE" --yes ${lib.escapeShellArgs (pairs renamedApps)})
    '')}
    ${base} "$@"
    ${lib.optionalString (replacedApps != { }) (onSite ''
      cd "$FRAPPE_BENCH_ROOT"
      installed="$(${benchBin} --site "$SITE" list-apps --format json 2>/dev/null \
        | ${lib.getExe pkgs.jq} -r --arg s "$SITE" '.[$s][]? // empty' 2>/dev/null || true)"
      for pair in ${lib.escapeShellArgs (pairs replacedApps)}; do
        old="''${pair%%=*}" new="''${pair#*=}"
        ${lib.getExe' pkgs.gnugrep "grep"} -qxF "$old" <<< "$installed" || continue
        if ! ${lib.getExe' pkgs.gnugrep "grep"} -qxF "$new" <<< "$installed"; then
          echo "reconcile-apps: $new replaces $old on $SITE: installing $new"
          ${benchBin} --site "$SITE" install-app "$new"
        fi
        echo "reconcile-apps: $new replaces $old on $SITE: uninstalling $old"
        ${benchBin} --site "$SITE" uninstall-app "$old" --yes --no-backup
      done
    '')}
  ''
