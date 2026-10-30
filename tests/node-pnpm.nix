# Checks for lib/bench.nix's pnpm path (python-devops-nix's lib.node over
# pnpm2nix) on tests/fixtures/node-pnpm: gamma is a pnpm 12 app shaped like
# frappe/suite (devEngines pins pnpm, a two-document lock, pnpm-workspace.yaml)
# whose frontend/ is a workspace member with no lock of its own; both carries
# a pnpm-lock.yaml and a yarn.lock. gamma's node_modules is built for real,
# offline, from the two registry tarballs its lock names, and linked into
# benchRoot.
{
  pkgs,
  checks,
  base,
  pnpm2nix,
}:

let
  inherit (pkgs) lib;

  bench =
    apps:
    import ../lib/bench.nix {
      inherit
        pkgs
        lib
        base
        pnpm2nix
        ;
      prodPythonEnv = pkgs.emptyDirectory;
      inherit (pkgs) nodejs;
      workspaceRoot = ./fixtures/node-pnpm;
      rootPyproject = {
        tool.uv.workspace.members = [ ];
      };
      appNames = apps;
    };

  gamma = bench [ "gamma" ];

  refused =
    let
      r = builtins.tryEval (bench [ "both" ]).nodeModules.both.drvPath;
    in
    if r.success then "no error" else "error";

  sh = lib.escapeShellArg;
in
{
  node-pnpm = checks.script "node-pnpm" { tools = [ pkgs.findutils ]; } ''
    fails=0
    ok()  { printf '  \033[32m✓\033[0m %s\n' "$1"; }
    no()  { printf '  \033[31m✗\033[0m %s\n' "$1"; fails=$((fails + 1)); }
    eq()  { if [ "$2" = "$3" ]; then ok "$1"; else no "$1"$'\n'"      expected: $2"$'\n'"      got:      $3"; fi; }

    echo "── evaluation ─────────────────────────────────────────────────"
    eq "the app is the one target: frontend/ is its workspace's member" "gamma" ${sh (lib.concatStringsSep " " gamma.nodeTargets)}
    eq "it builds from its pnpm lock" "apps/gamma/pnpm-lock.yaml" ${sh gamma.nodeLockSources.gamma}
    eq "no notices" "0" ${sh (toString (builtins.length gamma.nodeLockNotices))}
    eq "the shell gets the pnpm devEngines names" ${sh pkgs.pnpm_12.name} ${
      sh (lib.concatMapStringsSep " " (p: p.name) gamma.pnpmManagers)
    }
    eq "the app is installed in the shell" "gamma" ${sh (lib.concatStringsSep " " gamma.appsWithNode)}
    eq "pnpm-lock.yaml beside yarn.lock is refused" "error" ${sh refused}

    echo "── node_modules, offline ─────────────────────────────────────"
    nm=${gamma.nodeModules.gamma}
    eq "the member's dependency is installed under it" "yes" \
      "$([ -e "$nm/frontend/node_modules/is-number/package.json" ] && echo yes)"

    echo "── benchRoot ─────────────────────────────────────────────────"
    app=${gamma.benchRoot}/bench/apps/gamma
    eq "the app's node_modules is a real directory" "yes" \
      "$([ -d "$app/node_modules" ] && [ ! -L "$app/node_modules" ] && echo yes)"
    eq "…and so is the member's, linked to the store's packages" "yes" \
      "$([ -e "$app/frontend/node_modules/is-number/package.json" ] && echo yes)"
    eq "the member's code is the tree's" "yes" \
      "$([ -f "$app/frontend/package.json" ] && echo yes)"

    if [ "$fails" -gt 0 ]; then echo "$fails check(s) failed"; exit 1; fi
    echo "all node-pnpm checks passed" | tee "$out"
  '';
}
