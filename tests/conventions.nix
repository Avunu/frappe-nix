# python-devops-nix's conformance checks (its docs/conventions.md, Tests),
# registered through lib.checks.conventions with frappe-nix's tree, check
# names, modules, commands and the names it keeps:
#
#   ci-pins        every `uses:` in .github pinned by a full SHA, with its version
#   dependabot     the commit prefix, the cooldown and a catch-all group on each
#                  ecosystem; dev/'s uv tooling takes chore(deps)
#   check-names    each name in checks.<system> is <x> or <x>-<major>
#   env-vars       every FRAPPE_* name the tree uses is FRAPPE_NIX_* or
#                  FRAPPE_DEVGUARD_*, less the exceptions below
#   option-paths   the options the conventions name, under frappe-nix.* (the
#                  development shell) and services.frappe-nix.* (production)
#   config-blocks  sites.<name>.settings is a freeform JSON submodule
#   command-flags  `bench restore --help` and `frappe-init --help` list the
#                  shared flags
#
# option-paths and config-blocks evaluate a NixOS system, so they are Linux
# only, like tests/settings-nixos.nix. All of them evaluate or run in the
# sandbox; none needs a bench.
{
  self,
  pkgs,
  inputs,
}:

let
  inherit (pkgs) lib;
  inherit (pkgs.stdenv.hostPlatform) system;

  # The Python command `bench restore` execs (lib/scripts.nix, bench-restore),
  # without the shell's configuration, which `--help` does not read.
  benchCli = import ../lib/bench-cli.nix {
    inherit pkgs;
    inherit (import ../lib/standards/outputs.nix { inherit pkgs; }) frappeNixTools;
  };

  # The perSystem options of a bench built with the flakeModule
  # (tests/standards/fixtures/bench-flake.nix), and the NixOS options with
  # nixosModules.default. Only declarations are read.
  shellOptions = import ./standards/fixtures/bench-flake.nix {
    inherit self pkgs;
    part = "options";
  } (_: { });
  nixosOptions =
    (import (pkgs.path + "/nixos/lib/eval-config.nix") {
      inherit system;
      modules = [
        self.nixosModules.default
        {
          nixpkgs.pkgs = pkgs;
          system.stateVersion = lib.trivial.release;
        }
      ];
    }).options;
in
inputs.python-devops-nix.lib.checks.conventions (
  {
    inherit pkgs;
    src = ../.;
    product = "frappe-nix";
    # Only the names are read, so this can be the set the checks join.
    checks = self.checks.${system};
    # dev/ holds frappe-nix's own lint and type tooling (dev/pyproject.toml).
    dependabot.development = [
      {
        ecosystem = "uv";
        directory = "/dev";
      }
    ];
    commands = {
      restore = [
        benchCli
        "restore"
      ];
      scaffolder = lib.getExe self.packages.${system}.frappe-init;
    };
    # The FRAPPE_* names that may stay outside FRAPPE_NIX_*. One the tree no
    # longer uses, or that follows the rule, fails the check.
    envVars.exceptions = [
      # Frappe's own (frappe/config.py, frappe/utils, node_utils.js), which the
      # modules set for it.
      "FRAPPE_BENCH_ROOT"
      "FRAPPE_DB_HOST"
      "FRAPPE_DB_NAME"
      "FRAPPE_DB_PORT"
      "FRAPPE_DB_SOCKET"
      "FRAPPE_DB_TYPE"
      "FRAPPE_DB_USER"
      "FRAPPE_REDIS_CACHE"
      "FRAPPE_REDIS_QUEUE"
      "FRAPPE_SITE"
      "FRAPPE_SOCKETIO_PORT"
      "FRAPPE_SOCKETIO_UDS"
      "FRAPPE_STREAM_LOGGING"
      "FRAPPE_TUNE_GC"
      # frappe-runtime's own, as Frappe's are.
      "FRAPPE_SERVE_ASSETS"
      "FRAPPE_WEB_THREADS"
      # The old names of FRAPPE_NIX_* variables, read with a warning until
      # 2.0.0, which removes them (#182).
      "FRAPPE_BACKUP_CACHE"
      "FRAPPE_BACKUP_MC"
      "FRAPPE_BACKUP_SOURCE"
      "FRAPPE_BENCH_UPDATE_FORCE"
      "FRAPPE_LOG_LEVEL"
      "FRAPPE_OFFLINE_MIGRATE"
      "FRAPPE_OFFLINE_MIGRATE_PT_OSC"
      "FRAPPE_OFFLINE_MIGRATE_ROW_THRESHOLD"
      "FRAPPE_REPLICA_DB_SOCKET"
      "FRAPPE_RESTORE_ALLOW_UNGUARDED"
      "FRAPPE_TEST_ADMIN_PASSWORD"
      "FRAPPE_WEB_SOCKET"
      # frappe-init's template keys (frappe_init/template.py), not variables.
      "FRAPPE_BRANCH"
      "FRAPPE_VERSION"
      # frappe-nix's own names outside the rule, still to be renamed to
      # FRAPPE_NIX_*, dropped or kept with a reason (each one then leaves
      # this list): frappe_unixsock's off switch, two the modules set that
      # Frappe 16 does not read, the dev shell's frappe checkout, two the
      # nightly script exports to an app's suites, and selftest-assets'
      # branch override.
      "FRAPPE_UNIXSOCK_ENABLED"
      "FRAPPE_ENV_TYPE"
      "FRAPPE_REDIS_SOCKETIO"
      "FRAPPE_PATH"
      "FRAPPE_ADMIN_PASSWORD"
      "FRAPPE_SITE_URL"
      "FRAPPE_REF"
    ];
  }
  // lib.optionalAttrs pkgs.stdenv.hostPlatform.isLinux {
    options = {
      shell = shellOptions;
      nixos = nixosOptions;
    };
    tenant = {
      shell = "defaultSite";
      nixos = "sites";
    };
    settings = [
      {
        module = "nixos";
        path = [
          "sites"
          "<name>"
          "settings"
        ];
        format = "json";
      }
    ];
  }
)
