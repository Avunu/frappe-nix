# The journald contract services.frappe shares with odoo-nix and wordpress-nix,
# checked at evaluation: every unit the module defines carries APP_SERVICE (and
# APP_SITE when it serves one site), the per-site ones a stable
# SyslogIdentifier, and services.frappe.logging reaches the environment and
# nginx. A host shipping the journal to a log store selects by these fields, so
# a unit added without them is a unit whose logs nobody finds.
#
# Pure evaluation of two NixOS configurations — the unified runtime and the
# split units — against a stub bench that is never built. tests/socket-runtime.nix
# covers the same fields end to end, in a VM, for the runtime topology.
{ self, pkgs }:

let
  inherit (pkgs) lib;

  site = "erp.example.com";

  stubBench =
    pkgs.runCommand "stub-bench"
      {
        passthru = {
          pythonEnv = pkgs.emptyDirectory;
          inherit (pkgs) nodejs;
          appsPath = _: "/stub/apps";
        };
      }
      ''
        mkdir -p $out/bench
      '';

  eval =
    frappe:
    (import (pkgs.path + "/nixos/lib/eval-config.nix") {
      inherit (pkgs.stdenv.hostPlatform) system;
      modules = [
        self.nixosModules.default
        {
          nixpkgs.pkgs = pkgs;
          system.stateVersion = lib.trivial.release;
          services.frappe = lib.recursiveUpdate {
            enable = true;
            package = stubBench;
            redis.createLocally = true;
            sites.${site} = {
              enable = true;
              database.createLocally = true;
              # Brings frappe-db-password-<site> into being.
              database.passwordFile = "/run/secrets/db-password";
              nginx.enable = true;
            };
          } frappe;
        }
      ];
    }).config;

  runtime = eval { };
  split = eval {
    runtime.enable = false;
    logging = {
      level = "debug";
      accessLog = false;
    };
  };

  fieldsOf =
    config: unit: toString (config.systemd.services.${unit}.serviceConfig.LogExtraFields or [ ]);
  idOf = config: unit: config.systemd.services.${unit}.serviceConfig.SyslogIdentifier or "";
  envOf =
    config: unit: var:
    config.systemd.services.${unit}.environment.${var} or "";

  # Every unit the module generates is named frappe-*; none may be unlabelled.
  unlabelled =
    config:
    toString (
      lib.filter (
        unit:
        lib.hasPrefix "frappe-" unit
        && !(lib.elem "APP_SITE=${site}" (
          config.systemd.services.${unit}.serviceConfig.LogExtraFields or [ ]
        ))
      ) (builtins.attrNames config.systemd.services)
    );

  perSite = "APP_SITE=${site}";
in
{
  logging-fields = pkgs.runCommand "frappe-nix-logging-fields-check" { } ''
    fails=0
    ok()  { printf '  \033[32m✓\033[0m %s\n' "$1"; }
    no()  { printf '  \033[31m✗\033[0m %s\n' "$1"; fails=$((fails + 1)); }
    eq()  { if [ "$2" = "$3" ]; then ok "$1"; else no "$1"$'\n'"      expected: $2"$'\n'"      got:      $3"; fi; }
    has() { case "$3" in *"$2"*) ok "$1" ;; *) no "$1"$'\n'"      expected to contain: $2"$'\n'"      got: $3" ;; esac; }

    echo "unified runtime:"
    eq "the runtime unit"            "APP_SERVICE=runtime ${perSite}" ${lib.escapeShellArg (fieldsOf runtime "frappe-${site}")}
    eq "...and its identifier"       "frappe-runtime"                 ${lib.escapeShellArg (idOf runtime "frappe-${site}")}
    eq "init"                        "APP_SERVICE=init ${perSite}"    ${lib.escapeShellArg (fieldsOf runtime "frappe-init-${site}")}
    eq "...and its identifier"       "frappe-init"                    ${lib.escapeShellArg (idOf runtime "frappe-init-${site}")}
    eq "the db-password oneshot is init" "APP_SERVICE=init ${perSite}" ${lib.escapeShellArg (fieldsOf runtime "frappe-db-password-${site}")}
    eq "...with an identifier of its own" "frappe-db-password"        ${lib.escapeShellArg (idOf runtime "frappe-db-password-${site}")}
    eq "migrate"                     "APP_SERVICE=migrate ${perSite}" ${lib.escapeShellArg (fieldsOf runtime "frappe-migrate-${site}")}
    eq "...and its identifier"       "frappe-migrate"                 ${lib.escapeShellArg (idOf runtime "frappe-migrate-${site}")}
    eq "mysql: shared, so no site"   "APP_SERVICE=db"                 ${lib.escapeShellArg (fieldsOf runtime "mysql")}
    eq "redis: shared, so no site"   "APP_SERVICE=redis"              ${lib.escapeShellArg (fieldsOf runtime "redis-frappe")}
    eq "nginx: shared, so no site"   "APP_SERVICE=nginx"              ${lib.escapeShellArg (fieldsOf runtime "nginx")}
    eq "no frappe-* unit without the site's fields" "" ${lib.escapeShellArg (unlabelled runtime)}
    eq "FRAPPE_LOG_LEVEL defaults to warning" "WARNING" ${
      lib.escapeShellArg (envOf runtime "frappe-${site}" "FRAPPE_LOG_LEVEL")
    }
    eq "Python output is unbuffered" "1" ${
      lib.escapeShellArg (envOf runtime "frappe-${site}" "PYTHONUNBUFFERED")
    }
    has "the access log goes to the journal as JSON" \
      "access_log syslog:server=unix:/dev/log,tag=nginx_access,nohostname journal_json;" \
      ${lib.escapeShellArg runtime.services.nginx.commonHttpConfig}
    has "...in the agreed format" \
      '"status":$status,"bytes":$body_bytes_sent,"request_time":$request_time,"upstream_time":"$upstream_response_time"' \
      ${lib.escapeShellArg runtime.services.nginx.commonHttpConfig}
    has "nginx's error log keeps its severities" "syslog:server=unix:/dev/log" \
      ${lib.escapeShellArg runtime.services.nginx.logError}

    echo "split units, logging.level = debug, logging.accessLog = false:"
    eq "web"                         "APP_SERVICE=web ${perSite}"       ${lib.escapeShellArg (fieldsOf split "frappe-web-${site}")}
    eq "...and its identifier"       "frappe-web"                       ${lib.escapeShellArg (idOf split "frappe-web-${site}")}
    eq "scheduler"                   "APP_SERVICE=scheduler ${perSite}" ${lib.escapeShellArg (fieldsOf split "frappe-scheduler-${site}")}
    eq "socketio"                    "APP_SERVICE=socketio ${perSite}"  ${lib.escapeShellArg (fieldsOf split "frappe-socketio-${site}")}
    eq "a worker"                    "APP_SERVICE=worker ${perSite}"    ${lib.escapeShellArg (fieldsOf split "frappe-worker-short-${site}")}
    eq "...identified by its queue"  "frappe-worker-short"              ${lib.escapeShellArg (idOf split "frappe-worker-short-${site}")}
    eq "no frappe-* unit without the site's fields" "" ${lib.escapeShellArg (unlabelled split)}
    eq "FRAPPE_LOG_LEVEL follows the option" "DEBUG" ${
      lib.escapeShellArg (envOf split "frappe-web-${site}" "FRAPPE_LOG_LEVEL")
    }
    has "the access log is off" "access_log off;" ${lib.escapeShellArg split.services.nginx.commonHttpConfig}

    if [ "$fails" -ne 0 ]; then
      echo "$fails check(s) failed" >&2
      exit 1
    fi
    echo "all logging-fields checks passed" | tee "$out"
  '';
}
