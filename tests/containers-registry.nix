# The names the container images are built under, with and without
# containers.registry: <name>/<role> bare, <registry>/<name>/<role>
# with one, a trailing slash on the registry not doubled. odoo-nix applies the
# same rule (its tests/containers-eval.nix).
#
# Pure evaluation: a consumer's flake goes through lib.mkFlake and only each
# image's imageName is read, so nothing is built and the workspace is never
# looked into.
{ self, pkgs }:

let
  inherit (pkgs) lib;
  inherit (pkgs.stdenv.hostPlatform) system;

  imageNames =
    {
      registry,
      runtime ? true,
    }:
    let
      packages =
        (self.lib.mkFlake
          {
            inputs.self = {
              inherit (self) outPath inputs;
            };
          }
          {
            systems = [ system ];
            imports = [ self.flakeModules.default ];
            perSystem.frappe-nix = {
              enable = true;
              name = "acme";
              workspaceRoot = pkgs.emptyDirectory;
              runtime.enable = runtime;
              containers = {
                enable = true;
                inherit registry;
              };
            };
          }
        ).packages.${system};
      images =
        if runtime then
          [
            "runtime"
            "nginx"
            "bench-cli"
          ]
        else
          [
            "web"
            "scheduler"
            "worker-default"
            "socketio"
            "nginx"
            "bench-cli"
          ];
    in
    toString (map (p: packages.${p}.imageName) images);

  bare = imageNames { registry = ""; };
  registry = imageNames { registry = "ghcr.io/avunu"; };
  slash = imageNames { registry = "ghcr.io/avunu/"; };
  split = imageNames {
    registry = "ghcr.io/avunu";
    runtime = false;
  };
in
{
  containers-registry = pkgs.runCommand "frappe-nix-containers-registry-check" { } ''
    fails=0
    ok() { printf '  \033[32m✓\033[0m %s\n' "$1"; }
    no() { printf '  \033[31m✗\033[0m %s\n' "$1"; fails=$((fails + 1)); }
    eq() { if [ "$2" = "$3" ]; then ok "$1"; else no "$1"$'\n'"      expected: $2"$'\n'"      got:      $3"; fi; }

    eq "no registry: <name>/<role>" \
      "acme/runtime acme/nginx acme/bench" ${lib.escapeShellArg bare}
    eq "a registry prefixes every image" \
      "ghcr.io/avunu/acme/runtime ghcr.io/avunu/acme/nginx ghcr.io/avunu/acme/bench" ${lib.escapeShellArg registry}
    eq "a trailing slash is not doubled" \
      "ghcr.io/avunu/acme/runtime ghcr.io/avunu/acme/nginx ghcr.io/avunu/acme/bench" ${lib.escapeShellArg slash}
    eq "the split image set takes it too" \
      "ghcr.io/avunu/acme/web ghcr.io/avunu/acme/scheduler ghcr.io/avunu/acme/worker-default ghcr.io/avunu/acme/socketio ghcr.io/avunu/acme/nginx ghcr.io/avunu/acme/bench" ${lib.escapeShellArg split}

    if [ "$fails" -ne 0 ]; then
      echo "$fails check(s) failed" >&2
      exit 1
    fi
    echo "all containers-registry checks passed" | tee "$out"
  '';
}
