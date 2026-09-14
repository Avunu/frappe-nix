# Checks for lib/bench.nix's choice of lock per node target, and the notices
# that go with it. Pure evaluation: the fixture bench is tests/fixtures/
# node-targets (alpha and alpha/desk ship a yarn.lock, beta does not) under
# each of the node-locks/ shapes in tests/fixtures/node-locks. Nothing is
# built — a target's node_modules is only named, never instantiated, so no
# fetch is planned and the network is never needed.
{ pkgs }:

let
  inherit (pkgs) lib;

  bench =
    variant: extra:
    import ../lib/bench.nix (
      {
        inherit pkgs lib;
        prodPythonEnv = pkgs.emptyDirectory;
        nodejs = pkgs.nodejs;
        workspaceRoot = ./fixtures/node-targets;
        rootPyproject = {
          tool.uv.workspace.members = [ ];
        };
        nodeLocksDir = ./fixtures/node-locks + "/${variant}";
      }
      // extra
    );

  sources = variant: (bench variant { }).nodeLockSources;
  notices = variant: (bench variant { }).nodeLockNotices;

  show =
    attrs:
    lib.concatStringsSep " " (lib.mapAttrsToList (k: v: "${k}=${if v == null then "-" else v}") attrs);
  sh = lib.escapeShellArg;
  count = xs: toString (builtins.length xs);
  has = needle: xs: toString (builtins.length (lib.filter (lib.hasInfix needle) xs));
in
{
  node-locks-precedence = pkgs.runCommand "frappe-nix-node-locks-precedence-check" { } ''
    fails=0
    ok()  { printf '  \033[32m✓\033[0m %s\n' "$1"; }
    no()  { printf '  \033[31m✗\033[0m %s\n' "$1"; fails=$((fails + 1)); }
    eq()  { if [ "$2" = "$3" ]; then ok "$1"; else no "$1"$'\n'"      expected: $2"$'\n'"      got:      $3"; fi; }

    echo "── no node-locks/ at all ─────────────────────────────────────"
    eq "an app with a yarn.lock builds from it; one without has no lock" \
      "alpha=apps/alpha/yarn.lock alpha/desk=apps/alpha/desk/yarn.lock beta=-" ${sh (show (sources "none"))}
    eq "and the missing one is the only notice" "missing: beta" ${sh (lib.concatStringsSep "|" (notices "none"))}

    echo "── a fallback for the app without a yarn.lock ────────────────"
    eq "beta builds from node-locks/beta/yarn.lock" "node-locks/beta/yarn.lock" ${sh (sources "fallback").beta}
    eq "the others still from their own" "apps/alpha/yarn.lock" ${sh (sources "fallback").alpha}
    eq "no notices" "0" ${sh (count (notices "fallback"))}
    eq "its node_modules derivation exists" "beta-node-modules" ${
      sh (bench "fallback" { }).nodeModules.beta.name
    }

    echo "── a fallback beside an upstream yarn.lock, not forced ───────"
    eq "the upstream lock wins" "apps/alpha/yarn.lock" ${sh (sources "unused").alpha}
    eq "and the fallback is reported unused, with both ways out" "1" \
      ${sh (
        has "node-locks/alpha is unused — apps/alpha ships a yarn.lock and builds from it. `git rm -r node-locks/alpha`, or make it a deliberate override: bench-update --node-locks alpha" (
          notices "unused"
        )
      )}

    echo "── a forced fallback ─────────────────────────────────────────"
    eq "the fallback wins over the upstream lock" "node-locks/alpha/yarn.lock" ${sh (sources "forced").alpha}
    eq "without an unused notice" "0" ${sh (has "unused" (notices "forced"))}
    eq "…while alpha/desk, not forced, keeps its own" "apps/alpha/desk/yarn.lock" ${
      sh (sources "forced")."alpha/desk"
    }

    echo "── stale stamps ──────────────────────────────────────────────"
    eq "a fallback older than the app's package.json" "1" \
      ${sh (
        has "node-locks/beta/yarn.lock is older than apps/beta's package.json — regenerate it: bench-update --node-locks beta" (
          notices "stale"
        )
      )}
    eq "a forced fallback older than the app's yarn.lock" "1" \
      ${sh (
        has "node-locks/alpha/yarn.lock is older than apps/alpha's yarn.lock — regenerate it: bench-update --node-locks alpha" (
          notices "stale"
        )
      )}
    eq "both still build from the fallback" "node-locks/alpha/yarn.lock node-locks/beta/yarn.lock" \
      ${sh "${(sources "stale").alpha} ${(sources "stale").beta}"}

    echo "── leftovers of the npm-based scheme ─────────────────────────"
    eq "a package-lock.json is noticed" "1" \
      ${sh (has "node-locks/alpha/package-lock.json is from the npm-based scheme" (notices "npm-era"))}
    eq "and ignored: alpha builds from its own lock" "apps/alpha/yarn.lock" ${sh (sources "npm-era").alpha}

    echo "── the messages follow the caller's labels (app mode) ────────"
    eq "the directory and the command are the caller's" "1" \
      ${sh (
        has "nix/node-locks/alpha is unused" (
          (bench "unused" {
            nodeLocksLabel = "nix/node-locks";
            nodeLocksCommand = "nix run .#relock -- --node-locks";
          }).nodeLockNotices
        )
      )}
    eq "…in the command too" "1" \
      ${sh (
        has "override: nix run .#relock -- --node-locks alpha" (
          (bench "unused" {
            nodeLocksLabel = "nix/node-locks";
            nodeLocksCommand = "nix run .#relock -- --node-locks";
          }).nodeLockNotices
        )
      )}

    echo "── an excluded frontend ──────────────────────────────────────"
    eq "is not a target, so it has no lock source" "alpha=apps/alpha/yarn.lock beta=-" \
      ${sh (show (bench "none" { nodeNestedFrontendExcludes = [ "alpha/desk" ]; }).nodeLockSources)}

    if [ "$fails" -gt 0 ]; then echo "$fails check(s) failed"; exit 1; fi
    echo "all node-locks-precedence checks passed" | tee "$out"
  '';
}
