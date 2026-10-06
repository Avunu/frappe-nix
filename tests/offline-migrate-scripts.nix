# `bench-migrate` and `bench-offline-migrate`, rendered from lib/scripts.nix and
# driven against stubs: what runs in front of the migrate, what stops it, and
# what the caller's environment still decides.
#
# Rendered rather than read, as tests/bench-restore.nix does: devenv never
# shellchecks a `scripts.<n>.exec` body, so without this the step in front of
# every migrate would ship unlinted and unexercised.
{ pkgs }:

let
  inherit (pkgs) lib;

  render =
    offlineMigrate:
    import ../lib/scripts.nix {
      inherit lib pkgs offlineMigrate;
      appsWithNode = [ ];
      benchBin = "bench";
      pythonBin = "python-stub";
    };

  on = render {
    enable = true;
    rowThreshold = 123456;
    ptOsc = "/stub/pt-osc";
  };
  off = render {
    enable = false;
    rowThreshold = 123456;
  };
in
{
  offline-migrate-scripts =
    pkgs.runCommand "frappe-nix-offline-migrate-scripts-check"
      {
        nativeBuildInputs = [
          pkgs.shellcheck
          pkgs.coreutils
        ];
        migrateOn = on.bench-migrate.exec;
        migrateOff = off.bench-migrate.exec;
        toolOn = on.bench-offline-migrate.exec;
        toolOff = off.bench-offline-migrate.exec;
        passAsFile = [
          "migrateOn"
          "migrateOff"
          "toolOn"
          "toolOff"
        ];
      }
      ''
        fails=0
        ok() { printf '  \033[32m✓\033[0m %s\n' "$1"; }
        no() { printf '  \033[31m✗\033[0m %s\n' "$1"; fails=$((fails + 1)); }
        check() { local d=$1; shift; if "$@" >/dev/null 2>&1; then ok "$d"; else no "$d"; fi; }

        # SC2164: these scripts cd to the bench root without `set -e`, as
        # bench-console and bench-build do; a missing root is for bench to report.
        for s in migrateOn migrateOff toolOn toolOff; do
          p="''$s"Path
          check "$s is clean under shellcheck" shellcheck -s bash -S warning -e SC2317 -e SC2164 "''${!p}"
        done

        # Stubs. The shebang is resolved now: /usr/bin/env is not in the sandbox.
        BIN="$PWD/bin"; mkdir -p "$BIN" "$PWD/bench"
        stub() { printf '#!%s\n%s\n' "$(command -v bash)" "$2" >"$BIN/$1"; chmod +x "$BIN/$1"; }
        stub bench 'echo "bench $*" >>"$LOG"'
        stub bench-offline-migrate 'echo "offline" >>"$LOG"; exit "''${STUB_RC:-0}"'
        stub python-stub 'echo "python $*" >>"$LOG"; echo "PTOSC=''${FRAPPE_OFFLINE_MIGRATE_PT_OSC:-} THRESHOLD=''${FRAPPE_OFFLINE_MIGRATE_ROW_THRESHOLD:-}" >>"$LOG"'
        export PATH="$BIN:$PATH" LOG="$PWD/log" FRAPPE_BENCH_ROOT="$PWD/bench" FRAPPE_SITE=erp.example.com

        run() { : >"$LOG"; bash "$1" "''${@:2}" >/dev/null 2>&1 && RC=0 || RC=$?; }
        log_is() { [ "$(cat "$LOG")" = "$2" ] && ok "$1" || no "$1"$'\n'"      expected: $2"$'\n'"      got:      $(cat "$LOG")"; }

        echo "bench-migrate, enabled:"
        run "$migrateOnPath"
        log_is "the offline step runs first, then the migrate" $'offline\nbench --site erp.example.com migrate'
        STUB_RC=3 run "$migrateOnPath" --skip-failing
        log_is "a failed offline step stops the migrate" "offline"
        [ "$RC" = 3 ] && ok "…with the step's own exit code" || no "…with the step's own exit code (got $RC)"
        run "$migrateOnPath" --help
        log_is "--help does not start an offline step" "bench --site erp.example.com migrate --help"
        FRAPPE_SITE= run "$migrateOnPath"
        log_is "no site, no offline step" "bench migrate"
        run "$migrateOnPath" --skip-failing
        log_is "the migrate's own flags still reach it" $'offline\nbench --site erp.example.com migrate --skip-failing'

        echo "bench-migrate, disabled (the default):"
        run "$migrateOffPath"
        log_is "just the migrate" "bench --site erp.example.com migrate"

        echo "bench-offline-migrate:"
        run "$toolOnPath" --plan
        log_is "runs the tool with the bench's interpreter and the caller's flags" "$(printf 'python %s --plan\nPTOSC=/stub/pt-osc THRESHOLD=123456' "${../lib/offline-migrate.py}")"
        FRAPPE_OFFLINE_MIGRATE_ROW_THRESHOLD=9 FRAPPE_OFFLINE_MIGRATE_PT_OSC=/mine/pt run "$toolOnPath"
        log_is "the caller's environment beats what Nix baked" "$(printf 'python %s\nPTOSC=/mine/pt THRESHOLD=9' "${../lib/offline-migrate.py}")"
        run "$toolOffPath" --plan
        log_is "with the feature off it still plans, with nothing baked but the threshold" "$(printf 'python %s --plan\nPTOSC= THRESHOLD=123456' "${../lib/offline-migrate.py}")"

        if [ "$fails" -ne 0 ]; then
          echo "$fails check(s) failed" >&2
          exit 1
        fi
        echo "all offline-migrate-scripts checks passed" | tee "$out"
      '';
}
