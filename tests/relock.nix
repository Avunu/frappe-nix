# `nix run .#relock` (lib/relock.nix), built once for a bench and once for app
# mode, and driven against stub uv, lock-generator and build-deps commands: what
# it locks where, with which arguments, what it writes back and stages.
{ pkgs, checks }:

let
  # One line per call: "<tool> <cwd> <args>", to $CALLS.
  stub =
    name: body:
    pkgs.writeShellScriptBin name ''
      printf '%s %s %s\n' "${name}" "$PWD" "$*" >> "$CALLS"
      ${body}
    '';
  uv = stub "uv" ''
    if [ "''${1:-}" = --version ]; then echo "uv 0.0.0-stub"; exit 0; fi
    if [ "''${1:-}" = lock ]; then
      [ -f uv.lock ] && printf 'seed %s\n' "$(head -n1 uv.lock)" >> "$CALLS"
      [ "''${UV_FAIL:-}" = 1 ] && { echo "error: no solution" >&2; exit 1; }
      printf '%s\n' "''${UV_LOCK:-version = 1}" > uv.lock
    fi
  '';
  nodeLocks = stub "frappe-nix-node-locks" ''
    # <flags…> <bench> <locks-dir> [targets…]: write one fallback, as a run would.
    for a in "$@"; do case "$a" in --*) shift ;; *) break ;; esac; done
    mkdir -p "$2/beta"
    printf '# yarn lockfile v1\n' > "$2/beta/yarn.lock"
    [ "''${NODE_LOCKS_FAIL:-}" = 1 ] && exit 1
    exit 0
  '';
  buildDeps = stub "build-deps-sync" "";

  workspace = pkgs.runCommand "relock-fixture-workspace" { } ''
    mkdir -p $out/apps/alpha $out/apps/beta
    printf '[project]\nname = "alpha-bench"\n' > $out/pyproject.toml
    printf '[project]\nname = "alpha"\n' > $out/apps/alpha/pyproject.toml
    printf '{"name":"beta"}\n' > $out/apps/beta/package.json
  '';

  relock =
    appMode:
    import ../lib/relock.nix {
      inherit pkgs appMode uv;
      nodeLocksTool = nodeLocks;
      nodeNestedFrontendExcludes = [ "alpha/desk" ];
      buildDepsSyncBin = "${buildDeps}/bin/build-deps-sync";
      workspace = if appMode then workspace else null;
      lockDir = "nix";
    };
in
{
  relock =
    checks.script "relock"
      {
        tools = with pkgs; [
          git
          coreutils
        ];
      }
      ''
        export HOME="$PWD"
        bash ${./relock.sh} \
          ${relock false}/bin/frappe-nix-relock \
          ${relock true}/bin/frappe-nix-relock \
          ${workspace} \
          2>&1 | tee "$out"
        grep -q '^all checks passed$' "$out"
      '';
}
