# The names the container images are built under, with and without
# containers.registry: <name>/<role> bare, <registry>/<name>/<role>
# with one, a trailing slash on the registry not doubled. odoo-nix applies the
# same rule (its tests/containers-eval.nix).
#
# The packages are container-<role>, and the names they had before
# (runtime, nginx, bench-cli, …) are aliases of them unless
# containers.aliases = false. Each image config (passthru.imageConfig) has
# the OCI labels and, where it serves HTTP, a healthcheck; it runs as root
# unless containers.nonRoot, then as 1000:1000 with HOME set.
#
# Pure evaluation: a consumer's flake goes through lib.mkFlake and only each
# image's imageName and config are read, so nothing is built and the
# workspace is never looked into.
{ self, pkgs }:

let
  inherit (pkgs) lib;
  inherit (pkgs.stdenv.hostPlatform) system;

  packagesFor =
    {
      registry ? "",
      runtime ? true,
      aliases ? true,
      nonRoot ? false,
    }:
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
            inherit registry aliases nonRoot;
          };
        };
      }
    ).packages.${system};

  roles =
    runtime:
    if runtime then
      [
        "runtime"
        "nginx"
        "bench"
      ]
    else
      [
        "web"
        "scheduler"
        "worker-default"
        "socketio"
        "nginx"
        "bench"
      ];

  imageNames =
    args:
    let
      packages = packagesFor args;
    in
    toString (map (role: packages."container-${role}".imageName) (roles (args.runtime or true)));

  bare = imageNames { registry = ""; };
  registry = imageNames { registry = "ghcr.io/avunu"; };
  slash = imageNames { registry = "ghcr.io/avunu/"; };
  split = imageNames {
    registry = "ghcr.io/avunu";
    runtime = false;
  };

  # The old names, read through the alias: the same image.
  withAliases = packagesFor { };
  aliased = toString (
    map (old: withAliases.${old}.imageName) [
      "runtime"
      "nginx"
      "bench-cli"
    ]
  );
  splitAliased =
    let
      packages = packagesFor { runtime = false; };
    in
    toString (
      map (old: packages.${old}.imageName) [
        "web"
        "scheduler"
        "worker-default"
        "worker-short"
        "worker-long"
        "socketio"
      ]
    );
  withoutAliases = toString (
    lib.filter
      (
        n:
        lib.elem n [
          "runtime"
          "nginx"
          "bench-cli"
        ]
      )
      (
        lib.attrNames (packagesFor {
          aliases = false;
        })
      )
  );

  # "<role>=<User>,<healthcheck>" for the unified set, then nginx's Env (the
  # only image whose Env names no store path, so reading it builds nothing).
  conventions =
    args:
    let
      packages = packagesFor args;
      describe =
        role:
        let
          c = packages."container-${role}".imageConfig;
        in
        "${role}=${c.User},${lib.boolToString (c ? Healthcheck)}";
    in
    "${toString (map describe (roles true))} env:${toString packages.container-nginx.imageConfig.Env}";
  splitHealth =
    let
      packages = packagesFor { runtime = false; };
    in
    toString (
      map (role: "${role}=${lib.boolToString (packages."container-${role}".imageConfig ? Healthcheck)}") (
        roles false
      )
    );
  runtimeLabels = (packagesFor { }).container-runtime.imageConfig.Labels;
  labelLine = "${runtimeLabels."org.opencontainers.image.title"} ${
    runtimeLabels."org.opencontainers.image.source"
  } ${toString (lib.attrNames runtimeLabels)}";
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
    eq "the old package names alias container-<role>" \
      "acme/runtime acme/nginx acme/bench" ${lib.escapeShellArg aliased}
    eq "the split set's old names alias it too" \
      "acme/web acme/scheduler acme/worker-default acme/worker-short acme/worker-long acme/socketio" ${lib.escapeShellArg splitAliased}
    eq "containers.aliases = false drops them" \
      "" ${lib.escapeShellArg withoutAliases}
    eq "by default the images run as root, as before" \
      "runtime=0:0,true nginx=0:0,true bench=0:0,false env:" ${lib.escapeShellArg (conventions { })}
    eq "containers.nonRoot runs them as 1000:1000 with HOME" \
      "runtime=1000:1000,true nginx=1000:1000,true bench=1000:1000,false env:HOME=/home/frappe" ${
        lib.escapeShellArg (conventions {
          nonRoot = true;
        })
      }
    eq "in the split set, only the HTTP roles have a healthcheck" \
      "web=true scheduler=false worker-default=false socketio=true nginx=true bench=false" ${lib.escapeShellArg splitHealth}
    eq "every image has the OCI labels" \
      "acme/runtime https://github.com/Avunu/frappe-nix org.opencontainers.image.licenses org.opencontainers.image.source org.opencontainers.image.title org.opencontainers.image.version" ${lib.escapeShellArg labelLine}

    if [ "$fails" -ne 0 ]; then
      echo "$fails check(s) failed" >&2
      exit 1
    fi
    echo "all containers-registry checks passed" | tee "$out"
  '';
}
