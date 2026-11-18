# Checks for lib/node-targets.nix — which apps/<x> and apps/<x>/<y> get a node
# lock and a node_modules. Pure evaluation over tests/fixtures/node-targets,
# the same tree tests/node-locks.sh drives the shell discovery over.
{ pkgs, checks }:

let
  inherit (pkgs) lib;

  targets = import ../lib/node-targets.nix { inherit lib; };

  apps = ./fixtures/node-targets/apps;
  names = builtins.attrNames (builtins.readDir apps);
  appSrcOf = app: apps + "/${app}";

  keysOf = args: map (t: t.key) (targets.discover ({ inherit names appSrcOf; } // args));

  all = targets.discover { inherit names appSrcOf; };
  desk = lib.findFirst (t: t.key == "alpha/desk") null all;
  panel = lib.findFirst (t: t.key == "alpha/tools/panel") null all;
in
{
  node-targets = checks.script "node-targets" { } ''
    fails=0
    ok()  { printf '  \033[32m✓\033[0m %s\n' "$1"; }
    no()  { printf '  \033[31m✗\033[0m %s\n' "$1"; fails=$((fails + 1)); }
    eq()  { if [ "$2" = "$3" ]; then ok "$1"; else no "$1"$'\n'"      expected: $2"$'\n'"      got:      $3"; fi; }

    # alpha (root + desk), beta (no yarn.lock — still a target); not alpha's
    # node_modules, not its .gitmodules-listed vendored/, and nothing under
    # gamma, which has no package.json of its own. Two levels down,
    # tools/panel ships a yarn.lock and is one; tools/nolock, with a
    # package-lock.json only, is not.
    eq "an app with package.json and its nested frontends are targets; nothing else is" \
      "alpha alpha/desk alpha/tools/panel beta" \
      ${lib.escapeShellArg (lib.concatStringsSep " " (keysOf { }))}

    eq "an excluded frontend is left out" \
      "alpha beta" \
      ${lib.escapeShellArg (
        lib.concatStringsSep " " (keysOf {
          excludes = [
            "alpha/desk"
            "alpha/tools/panel"
          ];
        })
      )}

    eq "excluding a directory leaves out what is under it" \
      "alpha alpha/desk beta" \
      ${lib.escapeShellArg (
        lib.concatStringsSep " " (keysOf {
          excludes = [ "alpha/tools" ];
        })
      )}

    eq "a deeper target's subdir is its path under the app" \
      "alpha tools/panel" \
      ${lib.escapeShellArg "${panel.app} ${panel.subdir}"}

    eq "a nested target knows its app and subdir" \
      "alpha desk" \
      ${lib.escapeShellArg "${desk.app} ${desk.subdir}"}

    eq "the app order is the caller's" \
      "beta alpha alpha/desk alpha/tools/panel" \
      ${lib.escapeShellArg (
        lib.concatStringsSep " " (
          map (t: t.key) (
            targets.discover {
              names = [
                "beta"
                "alpha"
              ];
              inherit appSrcOf;
            }
          )
        )
      )}

    if [ "$fails" -gt 0 ]; then echo "$fails check(s) failed"; exit 1; fi
    echo "all node-targets checks passed" | tee "$out"
  '';
}
