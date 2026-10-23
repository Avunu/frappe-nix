# What services.frappe-nix takes from python-devops-nix's production pieces,
# checked at evaluation:
#
#   * by default (hardening.enable = false, through 1.x) no unit carries any
#     of the sandbox;
#   * with hardening.enable, every unit the module defines runs under lib.nixos.hardening, writes only
#     its site directory, and names the profile for what it runs: the Python
#     units and migrate `jit` (Frappe starts Node for a Website Theme), plus
#     `chromium` / `wkhtmltopdf` when those are on; socket.io `jit`; the
#     scheduler and init none; init and the password sync only unix sockets;
#   * no unit it defines has a UMask (frappe-nix#83);
#   * a host can relax one key on one unit without a conflict;
#   * environmentFiles reaches the units that run Frappe code, and the root
#     `bench`;
#   * database.slowQueryMs turns MariaDB's slow query log on;
#   * the socket directories: 0770 where nginx creates its socket, 0750 where
#     only the site's own sockets are.
#
# Pure evaluation of NixOS configurations against a stub bench that is never
# built, like tests/logging-fields.nix. The VM tests (migrate-rollback, socket,
# socket-runtime, pdf-renderers) run the same units under the sandbox.
{
  self,
  pkgs,
  checks,
}:

let
  inherit (pkgs) lib;

  site = "erp.example.com";
  siteDir = "/var/lib/frappe/${site}";

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
    frappe: extra:
    (import (pkgs.path + "/nixos/lib/eval-config.nix") {
      inherit (pkgs.stdenv.hostPlatform) system;
      modules = [
        self.nixosModules.default
        {
          nixpkgs.pkgs = pkgs;
          system.stateVersion = lib.trivial.release;
          # No root file system or boot loader to declare, so the assertions
          # left are the module's.
          boot.isContainer = true;
          services.frappe-nix = lib.recursiveUpdate {
            enable = true;
            # Opt-in through 1.x; `default` below leaves it off.
            hardening.enable = true;
            package = stubBench;
            redis.createLocally = true;
            sites.${site} = {
              enable = true;
              database.createLocally = true;
              database.passwordFile = "/run/secrets/db-password";
              nginx.enable = true;
            };
          } frappe;
        }
        extra
      ];
    }).config;

  runtime = eval {
    environmentFiles = [ "/run/secrets/frappe-env" ];
    database.slowQueryMs = 250;
    sites.${site} = {
      web.socketPath = "/run/frappe-erp/web.sock";
      nginx.socketPath = "/run/frappe-erp/nginx.sock";
    };
  } { };
  split = eval {
    runtime.enable = false;
    chromium.enable = true;
    wkhtmltopdf.enable = true;
    # A web socket alone in its directory: nginx only connects to it.
    sites.${site}.web.socketPath = "/run/frappe-web/web.sock";
  } { };
  # A host relaxing one key on one unit, as lib.nixos.hardening promises.
  relaxed = eval { } {
    systemd.services."frappe-${site}".serviceConfig = {
      MemoryDenyWriteExecute = false;
      ProtectHome = "read-only";
      ReadWritePaths = [ "/srv/extra" ];
    };
  };

  # The default: hardening.enable is false, and no unit carries any of it.
  default = eval { hardening.enable = false; } { };
  hardeningKeys = lib.attrNames self.inputs.python-devops-nix.lib.nixos.hardening.baseline ++ [
    "RestrictAddressFamilies"
    "ReadWritePaths"
  ];
  withHardening =
    config:
    toString (
      lib.filter (unit: lib.any (k: (sc config unit) ? ${k}) hardeningKeys) (frappeUnits config)
    );

  sc = config: unit: config.systemd.services.${unit}.serviceConfig;
  show =
    value:
    if builtins.isBool value then
      lib.boolToString value
    else if builtins.isList value then
      toString value
    else
      toString value;
  key =
    config: unit: name:
    show ((sc config unit).${name} or "");

  frappeUnits =
    config:
    lib.filter (unit: lib.hasPrefix "frappe-" unit) (builtins.attrNames config.systemd.services);
  withUMask = config: toString (lib.filter (unit: (sc config unit) ? UMask) (frappeUnits config));
  unsandboxed =
    config:
    toString (lib.filter (unit: (sc config unit).ProtectSystem or "" != "strict") (frappeUnits config));

  rule = config: dir: toString (lib.filter (lib.hasPrefix "d ${dir} ") config.systemd.tmpfiles.rules);

  cases = [
    # [ label expected actual ]
    [
      "by default (hardening.enable = false) no frappe-* unit has a sandbox key"
      ""
      (withHardening default)
    ]
    [
      "every frappe-* unit is sandboxed (runtime topology)"
      ""
      (unsandboxed runtime)
    ]
    [
      "…and in the split topology"
      ""
      (unsandboxed split)
    ]
    [
      "no frappe-* unit has a UMask (frappe-nix#83)"
      ""
      (withUMask runtime + withUMask split)
    ]
    [
      "the runtime writes its site directory"
      siteDir
      (key runtime "frappe-${site}" "ReadWritePaths")
    ]
    [
      "…and may start Node (jit)"
      "false"
      (key runtime "frappe-${site}" "MemoryDenyWriteExecute")
    ]
    [
      "…but nothing privileged"
      "@system-service ~@privileged"
      (key runtime "frappe-${site}" "SystemCallFilter")
    ]
    [
      "…with no capabilities"
      ""
      (key runtime "frappe-${site}" "CapabilityBoundingSet")
    ]
    [
      "…over unix and IP sockets"
      "AF_UNIX AF_INET AF_INET6"
      (key runtime "frappe-${site}" "RestrictAddressFamilies")
    ]
    [
      "init writes the site directory"
      siteDir
      (key runtime "frappe-init-${site}" "ReadWritePaths")
    ]
    [
      "…runs no JIT"
      "true"
      (key runtime "frappe-init-${site}" "MemoryDenyWriteExecute")
    ]
    [
      "…and talks over unix sockets only"
      "AF_UNIX"
      (key runtime "frappe-init-${site}" "RestrictAddressFamilies")
    ]
    [
      "the password sync talks over unix sockets only"
      "AF_UNIX"
      (key runtime "frappe-db-password-${site}" "RestrictAddressFamilies")
    ]
    [
      "…and writes nothing"
      ""
      (key runtime "frappe-db-password-${site}" "ReadWritePaths")
    ]
    [
      "migrate writes the site directory (snapshots included)"
      siteDir
      (key runtime "frappe-migrate-${site}" "ReadWritePaths")
    ]
    [
      "…may start Node for the Website Theme"
      "false"
      (key runtime "frappe-migrate-${site}" "MemoryDenyWriteExecute")
    ]
    [
      "…and reaches Redis over TCP"
      "AF_UNIX AF_INET AF_INET6"
      (key runtime "frappe-migrate-${site}" "RestrictAddressFamilies")
    ]
    [
      "with chromium.enable, gunicorn may drop capabilities (capset)"
      "@system-service"
      (key split "frappe-web-${site}" "SystemCallFilter")
    ]
    [
      "…set up user namespaces"
      "false"
      (key split "frappe-web-${site}" "RestrictNamespaces")
    ]
    [
      "…and open netlink"
      "AF_UNIX AF_INET AF_INET6 AF_NETLINK"
      (key split "frappe-web-${site}" "RestrictAddressFamilies")
    ]
    [
      "a worker renders PDFs too"
      "@system-service"
      (key split "frappe-worker-long-${site}" "SystemCallFilter")
    ]
    [
      "the scheduler only enqueues: no JIT, no renderer"
      "true @system-service ~@privileged"
      "${key split "frappe-scheduler-${site}" "MemoryDenyWriteExecute"} ${
        key split "frappe-scheduler-${site}" "SystemCallFilter"
      }"
    ]
    [
      "socket.io is Node: jit"
      "false"
      (key split "frappe-socketio-${site}" "MemoryDenyWriteExecute")
    ]
    [
      "…and writes nothing"
      ""
      (key split "frappe-socketio-${site}" "ReadWritePaths")
    ]
    [
      "a host relaxes one key on one unit"
      "false read-only"
      "${key relaxed "frappe-${site}" "MemoryDenyWriteExecute"} ${
        key relaxed "frappe-${site}" "ProtectHome"
      }"
    ]
    [
      "…and its ReadWritePaths add to the site's"
      "/srv/extra ${siteDir}"
      (toString (lib.sort lib.lessThan (sc relaxed "frappe-${site}").ReadWritePaths))
    ]
    [
      "environmentFiles reaches the runtime"
      "/run/secrets/frappe-env"
      (key runtime "frappe-${site}" "EnvironmentFile")
    ]
    [
      "…and migrate"
      "/run/secrets/frappe-env"
      (key runtime "frappe-migrate-${site}" "EnvironmentFile")
    ]
    [
      "…not init, which runs no Frappe code"
      ""
      (key runtime "frappe-init-${site}" "EnvironmentFile")
    ]
    [
      "none by default"
      ""
      (key split "frappe-web-${site}" "EnvironmentFile")
    ]
    [
      "slowQueryMs turns the slow query log on"
      "1"
      (show runtime.services.mysql.settings.mysqld.slow_query_log)
    ]
    [
      "…at that many milliseconds, in seconds"
      "0.250"
      runtime.services.mysql.settings.mysqld.long_query_time
    ]
    [
      "…and stays off by default"
      "no"
      (if split.services.mysql.settings.mysqld ? slow_query_log then "yes" else "no")
    ]
    [
      "nginx creates its socket beside the web socket: 0770"
      "d /run/frappe-erp 0770 frappe frappe -"
      (rule runtime "/run/frappe-erp")
    ]
    [
      "a directory with only the site's own socket: 0750"
      "d /run/frappe-web 0750 frappe frappe -"
      (rule split "/run/frappe-web")
    ]
  ];

  failingAssertions =
    config: toString (map (a: a.message) (lib.filter (a: !a.assertion) config.assertions));
  badSocket = eval { sites.${site}.web.socketPath = "/run/web.sock"; } { };

  assertionChecks = [
    [
      "the module's assertions hold"
      ""
      (failingAssertions runtime + failingAssertions split)
    ]
    [
      "a socket straight in /run is refused, by option"
      "services.frappe-nix.sites.\"${site}\".web.socketPath must be an absolute path inside its own directory (e.g. /run/frappe-${site}/web.sock), not directly in /run: the directory's mode is what keeps the socket private."
      (failingAssertions badSocket)
    ]
  ];

  results = map (
    c:
    let
      label = builtins.elemAt c 0;
      expected = builtins.elemAt c 1;
      actual = builtins.elemAt c 2;
    in
    if expected == actual then
      "ok   ${label}"
    else
      "FAIL ${label}\n       expected: ${expected}\n       got:      ${actual}"
  ) (cases ++ assertionChecks);
  failed = lib.filter (lib.hasPrefix "FAIL") results;
in
{
  production-unit = checks.script "production-unit" { } ''
    cat <<'EOF'
    ${lib.concatStringsSep "\n" results}
    EOF
    ${lib.optionalString (failed != [ ]) ''
      echo "${toString (builtins.length failed)} check(s) failed" >&2
      exit 1
    ''}
    echo "all production-unit checks passed" | tee "$out"
  '';
}
