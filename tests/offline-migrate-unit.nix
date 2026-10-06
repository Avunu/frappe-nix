# services.frappe.migrate.offline, checked at evaluation: what the per-site
# migrate unit's script does with it on and off, and in what order.
#
# Pure evaluation of two NixOS configurations against a stub bench that is never
# built, like tests/logging-fields.nix; the script is rendered (and so
# syntax-checked by writeShellScript) and read back. The migrate itself is not
# run: tests/migrate-rollback.nix covers the snapshot and rollback around it, and
# tests/test_offline_migrate.py covers the tool.
{ self, pkgs }:

let
  inherit (pkgs) lib;

  site = "erp.example.com";

  stubBench =
    pkgs.runCommand "stub-bench"
      {
        passthru = {
          inherit (pkgs) nodejs;
          pythonEnv = pkgs.emptyDirectory;
          appsPath = _: "/stub/apps";
        };
      }
      ''
        mkdir -p $out/bench
      '';

  eval =
    offline:
    (import (pkgs.path + "/nixos/lib/eval-config.nix") {
      inherit (pkgs.stdenv.hostPlatform) system;
      modules = [
        self.nixosModules.default
        {
          nixpkgs.pkgs = pkgs;
          system.stateVersion = lib.trivial.release;
          services.frappe = {
            enable = true;
            package = stubBench;
            redis.createLocally = true;
            migrate.offline = offline;
            sites.${site} = {
              enable = true;
              database.createLocally = true;
              database.passwordFile = "/run/secrets/db-password";
              nginx.enable = true;
            };
          };
        }
      ];
    }).config;

  scriptOf = config: config.systemd.services."frappe-migrate-${site}".serviceConfig.ExecStart;

  on = scriptOf (eval {
    enable = true;
    rowThreshold = 50000;
  });
  off = scriptOf (eval { });
in
{
  offline-migrate-unit = pkgs.runCommand "frappe-nix-offline-migrate-unit-check" { } ''
    fails=0
    ok() { printf '  \033[32m✓\033[0m %s\n' "$1"; }
    no() { printf '  \033[31m✗\033[0m %s\n' "$1"; fails=$((fails + 1)); }
    has() { if grep -qF -- "$2" "$3"; then ok "$1"; else no "$1 (missing: $2)"; fi; }
    lacks() { if grep -qF -- "$2" "$3"; then no "$1 (found: $2)"; else ok "$1"; fi; }
    line() { grep -nF -- "$1" "$2" | head -1 | cut -d: -f1; }

    echo "migrate.offline.enable = true:"
    has "the tool runs against the site" "offline-migrate.py --site ${site} --bench-root" ${on}
    has "with the bench's own interpreter" "/bin/python " ${on}
    has "and the threshold the option names" "FRAPPE_OFFLINE_MIGRATE_ROW_THRESHOLD=50000" ${on}
    has "and a pt-online-schema-change that carries its perl" "FRAPPE_OFFLINE_MIGRATE_PT_OSC=" ${on}
    has "a failure of it is the migrate's failure" "|| RC=\$?" ${on}
    has "bench migrate only runs if it passed" 'if [ "$RC" -eq 0 ]; then' ${on}
    offline_at="$(line "altering large tables online" ${on})"
    migrate_at="$(line "running bench migrate" ${on})"
    maint_at="$(line "set-maintenance-mode on" ${on})"
    if [ -n "$offline_at" ] && [ -n "$migrate_at" ] && [ "$offline_at" -lt "$migrate_at" ]; then
      ok "it runs before bench migrate"
    else
      no "it runs before bench migrate (offline at '$offline_at', migrate at '$migrate_at')"
    fi
    if [ -n "$maint_at" ] && [ "$maint_at" -lt "$offline_at" ]; then
      ok "…and inside maintenance mode"
    else
      no "…and inside maintenance mode (maintenance at '$maint_at', offline at '$offline_at')"
    fi

    echo "migrate.offline.enable = false (the default):"
    lacks "no offline step" "offline-migrate" ${off}
    lacks "no percona-toolkit" "FRAPPE_OFFLINE_MIGRATE_PT_OSC" ${off}
    has "bench migrate still runs" "running bench migrate" ${off}

    if [ "$fails" -ne 0 ]; then
      echo "$fails check(s) failed" >&2
      exit 1
    fi
    echo "all offline-migrate-unit checks passed" | tee "$out"
  '';
}
