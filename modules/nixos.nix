# NixOS systemd service module for Frappe bench production deployment.
#
# Multi-tenant: each site gets its own systemd unit instances and config.
# Stack (python, node, apps, assets) comes from the bench package's passthru —
# the module never takes pythonEnv/nodejs/benchRoot as options.
#
# On each new build the per-site frappe-migrate-<site> oneshot runs
# `bench migrate`. By default (services.frappe-nix.migrate.*) it first snapshots the
# database (mysqldump), verifies the snapshot and, if the migration fails,
# restores it and leaves the site in maintenance mode — see the `migrate`
# options below.
#
# With services.frappe-nix.hardening.enable, every unit it defines runs under
# python-devops-nix's systemd sandboxing baseline (lib.nixos.hardening;
# CONVENTIONS.md, Unit hardening): a read-only system but for the site's
# directory, no capabilities, a syscall filter, no W+X memory unless a unit
# names a profile for it. See `harden` below. Off by default through 1.x.
#
# Example:
#   services.frappe-nix = {
#     enable  = true;
#     package = inputs.bench.packages.x86_64-linux.default;
#     sites."mysite.example.com" = {
#       enable = true;
#       database.createLocally = true;
#       database.passwordFile  = config.age.secrets.db-pass.path;
#       encryptionKeyFile      = config.age.secrets.enc-key.path;
#       nginx.enable           = true;
#     };
#   };
#
# flake.nix applies this file to python-devops-nix's lib (lib.modules.importApply),
# so it is nixosModules.default, not this path, that a host imports.
{ base }:
{
  config,
  lib,
  pkgs,
  ...
}:
let
  inherit (lib)
    mkOption
    mkEnableOption
    mkIf
    mkMerge
    types
    mapAttrs
    mapAttrsToList
    nameValuePair
    filterAttrs
    concatStringsSep
    optionalAttrs
    optionalString
    ;

  # renamedApps/replacedApps: OLD = NEW, both app names. attrsOf checks only
  # the values; the names go into the migrate and reconcile scripts too.
  appPairs =
    let
      appName = "[a-z][a-z0-9_]*";
    in
    types.addCheck (types.attrsOf (types.strMatching appName)) (
      pairs: lib.all (old: builtins.match appName old != null) (lib.attrNames pairs)
    )
    // {
      description = "attribute set of app names (OLD = NEW), each matching [a-z][a-z0-9_]*";
    };

  inherit (base.python) threadCaps;

  cfg = config.services.frappe-nix;

  enabledSites = filterAttrs (_: s: s.enable) cfg.sites;

  # Resolve the effective package for a site (per-site override or top-level).
  sitePackage = siteCfg: if siteCfg.package != null then siteCfg.package else cfg.package;

  # Derive interpreter paths from a bench package's passthru.
  pkgBenchDir = pkg: "${pkg}/bench";
  pkgPythonEnv = pkg: pkg.passthru.pythonEnv;
  pkgNodejs = pkg: pkg.passthru.nodejs;
  pkgAppsPath = pkg: pkg.passthru.appsPath (pkgBenchDir pkg);

  # Host-local home of a site's scheduler lock. Frappe's lock is a flock on
  # config/scheduler_process, and siteDir may be a network filesystem: on
  # JuiceFS a flock lives in the metadata engine under the mount's session,
  # so an unlock lost while that engine is unreachable outlives the process
  # and every later scheduler start sees "already running" and returns.
  schedulerLockDir = name: "/run/frappe-scheduler/${name}";

  # The units that run a scheduler bind-mount schedulerLockDir's file over
  # config/scheduler_process (BindPaths), so the flock is host-local. Not a
  # symlink: filelock >= 3.20 opens the lock with O_NOFOLLOW, the symlink this
  # replaced made start_scheduler raise ELOOP, and the runtime ran ~30h with no
  # scheduler (2026-10-19). A hand-run `bench schedule` locks the plain file
  # underneath instead, so it no longer sees a unit's scheduler as running.
  schedulerLockBind =
    name: benchRoot: "${schedulerLockDir name}/scheduler_process:${benchRoot}/config/scheduler_process";

  # Before the config/ copy: remove a config/scheduler_process that is a
  # symlink (an earlier frappe-nix's; cp writes through it, and systemd binds
  # onto a symlink's target, which would keep the path a symlink and keep
  # ELOOP) or that the service user does not own (systemd's own, below), which
  # the copy's chmod would fail on. config/ is the service user's, so it can.
  clearSchedulerLock = benchRoot: ''
    if [ -L ${benchRoot}/config/scheduler_process ] \
      || { [ -e ${benchRoot}/config/scheduler_process ] && [ ! -O ${benchRoot}/config/scheduler_process ]; }; then
      rm -f ${benchRoot}/config/scheduler_process
    fi
  '';

  # After the copy: the BindPaths destination must exist as the service user's
  # regular file. A package whose config/ ships none would otherwise have
  # systemd create it, owned by root, and the next init's chmod fail on it.
  ensureSchedulerLock = benchRoot: ''
    touch ${benchRoot}/config/scheduler_process
  '';

  libraryPath = lib.makeLibraryPath [
    pkgs.zlib
    pkgs.openssl
    pkgs.libffi
    pkgs.file.out
    cfg.database.package.client
    pkgs.cairo
    pkgs.pango
    pkgs.gdk-pixbuf
    pkgs.harfbuzz
    pkgs.fontconfig
    pkgs.freetype
    pkgs.libjpeg
    pkgs.libpng
  ];

  # Env vars that depend only on the resolved package (interpreters, SSL,
  # library path) and not on any particular site's config. Shared by every
  # systemd unit's `environment=` (via siteEnv) and by the imperative
  # `bench` CLI wrapper, so a var like GIT_PYTHON_REFRESH only needs setting
  # once instead of being kept in sync by hand in two places.
  # threadCaps: BLAS and OpenMP pools at one thread (python-devops-nix's
  # lib.python.threadCaps). numpy, pandas or scipy, which an app may pull in,
  # otherwise size a pool to the host's CPU count on import in every worker,
  # which a request-serving process gains nothing from and which can fail
  # outright (`pthread_create failed`) on a many-core host.
  mkCoreEnv =
    _pkg:
    threadCaps
    // {
      DEV_SERVER = "0";
      FRAPPE_ENV_TYPE = "production";
      SSL_CERT_FILE = "${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt";
      LD_LIBRARY_PATH = libraryPath;
      # GitPython probes `git` on import; skip it, we ship git on PATH ourselves.
      GIT_PYTHON_REFRESH = "none";
    };

  # services.frappe-nix.environmentFiles on a unit that runs Frappe code: read
  # by systemd (root) when the unit starts, so the files may be root's 0400
  # (agenix) and never enter the store.
  environmentFileConfig = optionalAttrs (cfg.environmentFiles != [ ]) {
    EnvironmentFile = cfg.environmentFiles;
  };

  # Per-site environment. The package (and therefore interpreters) can differ
  # per site, so this is a function of (siteName, siteCfg).
  siteEnv =
    name: siteCfg:
    let
      pkg = sitePackage siteCfg;
    in
    mkCoreEnv pkg
    // {
      # The site's runtime bench tree (mkSiteInit), not the store path —
      # frappe.utils.get_bench_path() reads this directly to locate
      # config/ (scheduler lock/pid files), which must be writable.
      FRAPPE_BENCH_ROOT = "${siteCfg.siteDir}/bench";
      SITES_PATH = "${siteCfg.siteDir}/sites";
      FRAPPE_SITE = name;
      # Frappe's loggers to stderr, never to logs/*.log: stderr is the journal.
      # frappe_journald (grafted into the bench's virtualenv) forces the same for
      # a caller that asks for a file explicitly, and gives each line its <N>
      # priority prefix; see services.frappe-nix.logging.
      FRAPPE_STREAM_LOGGING = "1";
      # Read by frappe_journald as Frappe's default logger level. Frappe's own
      # production default is ERROR, which drops every frappe.logger().warning().
      FRAPPE_NIX_LOG_LEVEL = lib.toUpper cfg.logging.level;
      # Its name before (CONVENTIONS.md, Tool-owned variables), for a bench
      # package built from an older frappe-nix than this module, whose graft
      # reads only that. The graft prefers the new name. Removed with the
      # aliases in frappe-nix 2.0.0.
      FRAPPE_LOG_LEVEL = lib.toUpper cfg.logging.level;
      # A block-buffered stdout holds print() output back until the buffer fills
      # or the process exits, so a crash loses exactly the lines that explain it,
      # and what does arrive is out of order with stderr.
      PYTHONUNBUFFERED = "1";
      FRAPPE_TUNE_GC = "1";

      FRAPPE_DB_HOST = siteCfg.database.host;
      FRAPPE_DB_PORT = toString siteCfg.database.port;
      FRAPPE_DB_TYPE = "mariadb";

      FRAPPE_REDIS_CACHE = siteCfg.redis.cacheUrl;
      FRAPPE_REDIS_QUEUE = siteCfg.redis.queueUrl;
      FRAPPE_REDIS_SOCKETIO = siteCfg.redis.socketioUrl;

      FRAPPE_SOCKETIO_PORT = toString siteCfg.socketio.port;
    }
    // optionalAttrs (!cfg.runtime.enable && siteCfg.socketio.socketPath != "") {
      # realtime/index.js does `server.listen(uds || port)`, so this wins.
      # Unset under the unified runtime, which takes its listen address from
      # --uds and would otherwise read this as a second, unbound one.
      FRAPPE_SOCKETIO_UDS = siteCfg.socketio.socketPath;
    }
    // optionalAttrs (siteCfg.web.socketPath != "") {
      # Read by frappe_unixsock. gunicorn takes --bind unix: natively and does
      # not need it, but a `bench serve` run by hand on this host would
      # otherwise open a surprise port on 0.0.0.0.
      FRAPPE_NIX_WEB_SOCKET = siteCfg.web.socketPath;
      # The old name too, as FRAPPE_LOG_LEVEL above.
      FRAPPE_WEB_SOCKET = siteCfg.web.socketPath;
    }
    // optionalAttrs (siteCfg.database.socket != "") {
      FRAPPE_DB_SOCKET = siteCfg.database.socket;
    }
    // cfg.extraEnv;

  # The journald fields every unit this module defines carries, shared with
  # odoo-nix and wordpress-nix (python-devops-nix's lib.journald.fields;
  # CONVENTIONS.md, Journald vocabulary): a host shipping the journal to a log
  # store labels by these, so they are a contract, not decoration. APP_SERVICE
  # is the unit's role, one of lib.journald.roles; APP_SITE the site FQDN, only
  # for a unit tied to exactly one site; SyslogIdentifier frappe-<role> (or
  # `identifier`), stable whatever the process is called (`python3`, `bench`, a
  # store script named after the site), and not on the upstream units (mysql,
  # redis, nginx), which keep their own.
  #
  # LogExtraFields applies to everything journald receives from the unit's
  # processes — stdout/stderr, syslog(3) and sd_journal — so nginx's access log
  # over /dev/log is labelled as well as its stderr.
  logFields = args: base.journald.fields ({ prefix = "frappe"; } // args);

  # The sandbox for a unit this module defines: python-devops-nix's
  # lib.nixos.hardening, merged into its serviceConfig. Each key is a
  # mkDefault, so a host can relax one on one unit
  # (`systemd.services.<unit>.serviceConfig.<key> = …;`), and ReadWritePaths is
  # a plain list, so a host's paths add to the site directory. No UMask: the
  # units write files nginx serves through the group (frappe-nix#83), and the
  # migrate snapshots get their 0600 where they are written.
  #
  # ProtectSystem=strict leaves /run writable, so the socket directories and the
  # scheduler lock need no entry; the site directory does, and is the only
  # state a Frappe process writes outside its private /tmp: the runtime bench
  # tree (config/, logs/), sites/ and the snapshots all live under it. A host
  # whose siteDir holds a link to somewhere else adds that path itself.
  #
  # Opt-in through 1.x (services.frappe-nix.hardening.enable): off, a unit gets
  # none of these keys at all.
  harden = args: optionalAttrs cfg.hardening.enable (base.nixos.hardening args);

  # The profiles of a unit that runs Frappe code (the runtime, gunicorn, a
  # worker, migrate). `jit`: Frappe starts the bench's Node by bare name to
  # build a Website Theme's CSS (generate_bootstrap_theme.js, on saving a
  # theme and in after_migrate), and V8 aborts at start without W+X memory.
  # The PDF renderers, when they are on: a print can render in a request or a
  # job.
  frappeProfiles = [
    "jit"
  ]
  ++ lib.optional cfg.chromium.enable "chromium"
  ++ lib.optional cfg.wkhtmltopdf.enable "wkhtmltopdf";

  # Only unix sockets: a unit that reaches nothing over TCP.
  unixOnly = [ "AF_UNIX" ];

  # Packages on PATH for every Frappe service (git needed by GitPython).
  # systemd's `path` option sets PATH to exactly these packages' bin/sbin —
  # it does NOT fall back to /run/current-system/sw/bin (see
  # nixos/lib/systemd-lib.nix's environment.PATH = makeBinPath config.path),
  # so a package only in environment.systemPackages is invisible to these
  # services no matter what. cfg.extraPath is the escape hatch for callers
  # that need a CLI on these services' PATH (e.g. a custom app's
  # print-server subprocess calls).
  #
  # The four below are what frappe/utils/backups.py resolves with which() at
  # runtime, so they belong here rather than in each consumer's extraPath —
  # without them `bench backup` and every scheduled backup integration (S3,
  # Dropbox, Google Drive) is broken on any deployment of this module:
  #
  #   gzip     take_dump() throws ExecutableNotFound before dumping anything.
  #   database mariadb-dump/mysqldump, via frappe.database.get_command(); this
  #            is the *next* throw once gzip resolves. Same package already used
  #            for the migrate snapshot, so it costs no extra closure.
  #   gnutar   take_backup_of_files(). Still required even when a consumer only
  #            wants a DB backup: the S3/Dropbox/Drive jobs all call
  #            new_backup(ignore_files=False) unconditionally, and their
  #            backup_files flag gates only the *upload*, not the archiving.
  #   bash     execute_in_shell() runs the dump pipeline with
  #            executable=(shutil.which("bash") or "/bin/bash"), and NixOS
  #            ships /bin/sh but no /bin/bash — so the fallback raises
  #            FileNotFoundError rather than degrading to sh. The pipeline also
  #            needs a shell that understands `set -o pipefail`.
  #
  # gpg is deliberately absent: it is only reached under conf.encrypt_backup,
  # which is off by default, and pulling it in for every deployment to serve an
  # opt-in feature is not worth the closure. Consumers that enable backup
  # encryption add pkgs.gnupg via extraPath.
  #
  # file(1) is here for the same reason: `bench restore` (and anything else
  # Frappe runs through a shell) calls it to identify the archive. Without it a
  # site restore fails with "file: command not found" on any deployment.
  #
  # node is the bench's own Node, which Frappe calls by bare name (website
  # theme generation runs `node generate_bootstrap_theme.js`, so `migrate`
  # fails without it). Taking it from the bench package keeps it the same
  # version the socket.io and asset builds were made with.
  servicePath = [
    pkgs.git
    pkgs.gzip
    pkgs.gnutar
    pkgs.bash
    pkgs.file
    (pkgNodejs cfg.package)
    cfg.database.package
  ]
  ++ wkhtmltopdfPath
  ++ cfg.extraPath;

  # wkhtmltopdf, for print formats on Frappe's original PDF generator. Frappe
  # runs it by bare name, through pdfkit, which finds it by running `which`
  # first, so that has to be on PATH too. Unlike Chromium, nothing is linked.
  wkhtmltopdfPath = lib.optionals cfg.wkhtmltopdf.enable [
    cfg.wkhtmltopdf.package
    pkgs.which
  ];

  # Secret-bearing files for a site's init unit, keyed for both
  # systemd LoadCredential= and the jq merge expression below. Source files
  # (e.g. agenix's /run/agenix/*) are typically root:root 0400 — LoadCredential
  # has systemd (root) read them and re-expose them under $CREDENTIALS_DIRECTORY
  # owned by the unit's own User/Group, so the unit never needs direct access
  # to the original file.
  mkSiteCredentials =
    siteCfg:
    (lib.optional (siteCfg.database.passwordFile != null) {
      file = siteCfg.database.passwordFile;
      key = "db_password";
    })
    ++ (lib.optional (siteCfg.encryptionKeyFile != null) {
      file = siteCfg.encryptionKeyFile;
      key = "encryption_key";
    })
    ++ lib.imap1 (i: f: {
      file = f;
      key = "extra_config_${toString i}";
    }) siteCfg.settingsFiles;

  # Script wrapper that sets PYTHONPATH from the package's apps and execs.
  # cwd is left to systemd's WorkingDirectory= (set per-service in
  # mkSiteServices) rather than `cd`-ing here — one declarative source of truth
  # instead of two that can drift apart. It is the runtime bench dir for
  # everything that shells out to the `bench` CLI, and the sites dir for
  # gunicorn; both follow upstream's supervisor.conf, and the reasoning is on
  # mkService and on frappe-web there.
  mkExec =
    pkg: name: cmd:
    pkgs.writeShellScript "frappe-${name}" ''
      set -euo pipefail
      export PYTHONPATH="${pkgAppsPath pkg}"
      exec ${cmd}
    '';

  # The app registry (sites/apps.txt, sites/apps.json) is the package's, never
  # a copy of it. apps.txt is what frappe.get_all_apps() returns, and a copy
  # that fell behind the package would silently hide an app from every
  # process on this host. So: links into the store, like the assets below.
  # `ln -sfn` also replaces the regular file an older frappe-nix left here
  # with its copy-once seed. And frappe only ever reads these; anything that
  # tries to write one (an upstream `bench get-app` run by hand) fails on the
  # read-only store, which is the right outcome for an unmanaged mutation.
  linkRegistry = benchDir: sitesPath: ''
    mkdir -p ${sitesPath}
    for f in apps.txt apps.json; do
      if [ -e "${benchDir}/sites/$f" ]; then
        ln -sfn "${benchDir}/sites/$f" "${sitesPath}/$f"
      fi
    done
  '';

  # Chromium, for Frappe's "chrome" PDF generator and Print Designer. Both look
  # for it at <bench>/chromium/chrome-linux/headless_shell, where `bench
  # setup-chrome` downloads Google's chrome-headless-shell, unless
  # common_site_config.json names a `chromium_path`. Left to themselves they
  # download it, and that build does not run on NixOS: it expects an FHS dynamic
  # loader. Print Designer retries the download from a scheduler event that
  # fires every few minutes.
  #
  # So link a NixOS build of it where they look. This needs nothing in
  # common_site_config.json, which is the operator's, and an operator's own
  # `chromium_path` still wins, since both check it first. The link is re-made
  # on every run, so it follows the package. The directory is removed only when
  # the binary is not already a link, which is the leftover of an earlier
  # download, so a routine run never leaves the path missing while a running
  # process could be looking for it.
  #
  # The default is the same chrome-headless-shell, patched for NixOS by nixpkgs'
  # Playwright packaging, not pkgs.chromium. Frappe starts a browser for every
  # PDF and stops it with SIGTERM. Full Chromium's headless mode makes a profile
  # under ~/.config for each start, which fails outright under this module's
  # root-owned home and, where it can write, leaves about 2 MB behind every
  # time, since SIGTERM skips the cleanup. The headless shell writes no profile,
  # and its closure is a quarter of the size.
  #
  # bin/ is added so lib.getExe finds it, as for any other chromium.package.
  # Chromium finds its resources through /proc/self/exe, which resolves the
  # link to the real directory.
  chromeHeadlessShell =
    let
      shell = pkgs.playwright-driver.components.chromium-headless-shell;
    in
    pkgs.runCommand "chrome-headless-shell" { meta.mainProgram = "chrome-headless-shell"; } ''
      mkdir -p $out/bin
      # The directory is named for the platform: -linux64, -linux-arm64.
      ln -s ${shell}/chrome-headless-shell-*/chrome-headless-shell $out/bin/chrome-headless-shell
      test -x $out/bin/chrome-headless-shell
    '';

  linkChromium =
    benchRoot:
    optionalString cfg.chromium.enable ''
      if [ ! -L "${benchRoot}/chromium/chrome-linux/headless_shell" ]; then
        rm -rf "${benchRoot}/chromium"
      fi
      mkdir -p "${benchRoot}/chromium/chrome-linux"
      ln -sfn ${lib.getExe cfg.chromium.package} "${benchRoot}/chromium/chrome-linux/headless_shell"
    '';

  # Per-site init script: assemble runtime bench tree, link the registry and
  # assets, seed sites dir, and synthesize site_config.json (merging secrets).
  mkSiteInit =
    name: siteCfg:
    let
      pkg = sitePackage siteCfg;
      benchDir = pkgBenchDir pkg;
      sitesPath = "${siteCfg.siteDir}/sites";
      runtimeBenchDir = "${siteCfg.siteDir}/bench";

      # Base site_config.json from Nix options (no secrets).
      baseConfig = {
        db_host = siteCfg.database.host;
        db_port = siteCfg.database.port;
        db_type = "mariadb";
        db_name = siteCfg.database.name;
        db_user = siteCfg.database.user;
        redis_cache = siteCfg.redis.cacheUrl;
        redis_queue = siteCfg.redis.queueUrl;
        redis_socketio = siteCfg.redis.socketioUrl;
        socketio_port = siteCfg.socketio.port;
      }
      // optionalAttrs (!cfg.runtime.enable && siteCfg.socketio.socketPath != "") {
        # Belt and braces with FRAPPE_SOCKETIO_UDS in the unit env: node_utils.js
        # merges this file too, so the realtime server still finds the socket if
        # it is ever started outside the unit.
        #
        # Not written under the unified runtime: nothing binds a separate realtime
        # socket there, and a stale socketio_uds would point anything that reads it
        # (a hand-run `frappe-realtime`, say) at a path with no listener.
        socketio_uds = siteCfg.socketio.socketPath;
      }
      // optionalAttrs (siteCfg.database.socket != "") {
        db_socket = siteCfg.database.socket;
      }
      // siteCfg.settings;

      baseConfigFile = pkgs.writeText "site-config-${name}.json" (builtins.toJSON baseConfig);

      # Secrets merged via systemd LoadCredential — see mkSiteCredentials.
      secretFiles =
        (lib.optional (siteCfg.database.passwordFile != null) {
          file = siteCfg.database.passwordFile;
          key = "db_password";
        })
        ++ (lib.optional (siteCfg.encryptionKeyFile != null) {
          file = siteCfg.encryptionKeyFile;
          key = "encryption_key";
        });

    in
    pkgs.writeShellScript "frappe-init-${name}" ''
      set -euo pipefail

      # Assemble runtime bench tree.
      mkdir -p ${runtimeBenchDir}/logs
      ln -sfn ${benchDir}/apps   ${runtimeBenchDir}/apps
      ln -sfn ${benchDir}/env    ${runtimeBenchDir}/env
      ln -sfn ${sitesPath}       ${runtimeBenchDir}/sites

      # config/ is not pure config — bench writes runtime state into it
      # (scheduler_process, site_config.lock, pids/), so it must be a real
      # writable tree, not a symlink into the read-only store. Re-copy on
      # every init run to stay in sync with the package; any in-progress
      # state gets reset, which is fine since dependent services restart
      # right after this unit anyway.

      mkdir -p ${runtimeBenchDir}/config
      ${clearSchedulerLock runtimeBenchDir}
      cp -rT ${benchDir}/config ${runtimeBenchDir}/config
      chmod -R u+w ${runtimeBenchDir}/config
      ${ensureSchedulerLock runtimeBenchDir}

      ${linkRegistry benchDir sitesPath}
      ${linkChromium runtimeBenchDir}

      # common_site_config.json is the operator's after the first boot: seeded
      # once from the package if it ships one, then never touched.
      if [ ! -e "${sitesPath}/common_site_config.json" ] \
         && [ -e "${benchDir}/sites/common_site_config.json" ]; then
        cp "${benchDir}/sites/common_site_config.json" "${sitesPath}/common_site_config.json"
      fi

      # Symlink compiled assets from the package.
      if [ -d "${benchDir}/sites/assets" ]; then
        ln -sfn ${benchDir}/sites/assets ${sitesPath}/assets

        # Frappe resolves some asset paths relative to the process cwd, not to
        # SITES_PATH -- get_assets_json() is frappe.read_file("assets/assets.json").
        # Upstream bench runs its processes from <bench>/sites, so that lands on
        # sites/assets; ours run from the bench root (WorkingDirectory in
        # mkSiteServices), where it would miss. This link makes the relative
        # form resolve from either cwd.
        #
        # It fails silently and durably without this. read_file() returns None
        # for a missing path instead of raising, get_assets_json() caches that
        # None in a *shared* Redis key with no TTL, and every desk and website
        # render then dies in bundled_asset() on
        #   AttributeError: 'NoneType' object has no attribute 'get'
        # for as long as the key survives -- which is across restarts, rebuilds
        # and switches, since nothing evicts it. A previously-cached good value
        # masks the bug indefinitely, so it surfaces not when the mistake is
        # made but whenever something next clears caches. Hit in production
        # 2026-09-09: latent for months, then `bench migrate` cleared caches
        # during a v15 -> v16 upgrade and took the whole site to HTTP 500.
        ln -sfn ${sitesPath}/assets ${runtimeBenchDir}/assets
      fi

      # Create site directory.
      mkdir -p ${sitesPath}/${name}

      # Synthesize site_config.json: base config + secrets + extra files.
      # Secret values are read from $CREDENTIALS_DIRECTORY (populated by
      # systemd's LoadCredential= on this unit) rather than the original
      # source paths, so this script never needs read access to those.
      ${
        let
          # Read secret values into env vars.
          readSecrets = concatStringsSep "\n" (
            map (
              s:
              ''SECRET_${
                lib.toUpper (builtins.replaceStrings [ "-" "." ] [ "_" "_" ] s.key)
              }="$(cat "$CREDENTIALS_DIRECTORY/${s.key}")"''
            ) secretFiles
          );
          exportSecrets = concatStringsSep "\n" (
            map (
              s: "export SECRET_${lib.toUpper (builtins.replaceStrings [ "-" "." ] [ "_" "_" ] s.key)}"
            ) secretFiles
          );

          # Build jq expression.
          jqExpr =
            let
              base = ".";
              withSecrets = concatStringsSep " | " (
                map (
                  s:
                  ''. + {"${s.key}": $ENV.SECRET_${
                    lib.toUpper (builtins.replaceStrings [ "-" "." ] [ "_" "_" ] s.key)
                  }}''
                ) secretFiles
              );
              # --slurpfile binds $extraN to an array of every JSON value in the
              # file, even when the file holds a single object — index [0] to get
              # the object itself before merging.
              extraMerges = lib.imap1 (i: _f: ". * $extra${toString i}[0]") siteCfg.settingsFiles;
            in
            concatStringsSep " | " ([ base ] ++ lib.optional (secretFiles != [ ]) withSecrets ++ extraMerges);

          extraSlurpArgs = concatStringsSep " " (
            lib.imap1 (
              i: _f: ''--slurpfile extra${toString i} "$CREDENTIALS_DIRECTORY/extra_config_${toString i}"''
            ) siteCfg.settingsFiles
          );
        in
        ''
          ${readSecrets}
          ${exportSecrets}
          ${pkgs.jq}/bin/jq '${jqExpr}' ${extraSlurpArgs} ${baseConfigFile} \
            > ${sitesPath}/${name}/site_config.json
          chmod 0600 ${sitesPath}/${name}/site_config.json
        ''
      }
    '';

  # services.mysql's `ensureUsers` only ever creates passwordless accounts
  # (`IDENTIFIED WITH unix_socket`, i.e. OS-peer auth) — there is no
  # declarative way to set a real password through that option. Frappe
  # connects over TCP with the password baked into site_config.json, so we
  # set/refresh it separately here using the same secret. Safe to rerun on
  # every deploy (`ALTER USER ... IDENTIFIED BY` is idempotent and swaps the
  # account onto password auth regardless of its previous auth plugin).
  mkSiteDbPasswordSync =
    name: siteCfg:
    pkgs.writeShellScript "frappe-db-password-${name}" ''
      set -euo pipefail
      PASS="$(cat "$CREDENTIALS_DIRECTORY/db_password")"
      ESCAPED=$(printf '%s' "$PASS" | sed "s/'/'''/g")
      echo "ALTER USER '${siteCfg.database.user}'@'localhost' IDENTIFIED BY '$ESCAPED';" \
        | ${cfg.database.package}/bin/mysql -N
    '';

  # Safe deploy-time migration (ExecStart of frappe-migrate-<site>).
  #
  # python-devops-nix's migrate flow (lib.nixos.migrate.script; CONVENTIONS.md,
  # Migrate on deploy) with this module's steps:
  #
  #   check      the build marker, then the uninstalled-site guard; exit 100
  #              (nothing to do) for either
  #   dump       mysqldump | gzip, into a 0600 file in <siteDir>/snapshots (0700)
  #   verify     gzip's check and mysqldump's closing "-- Dump completed" line:
  #              a dump cut short is removed and stops the deploy before it
  #              migrates
  #   migrate    maintenance mode on, the app renames and replacements, the
  #              offline migrate, `bench migrate`
  #   onSuccess  maintenance mode off, the marker
  #   restore    drop every table and view, then load the snapshot
  #   onFailure  say the site is left in maintenance mode
  #
  # A physical snapshot is the only real rollback here: `bench migrate` performs
  # DDL (CREATE/ALTER TABLE), which auto-commits in MariaDB and cannot be undone
  # in a transaction. A failed migrate leaves the site serving Frappe's
  # maintenance page rather than new code on the rolled-back (older) schema; the
  # services only order after this unit (RemainAfterExit), they do not require it.
  #
  # Runs entirely as cfg.user with the site's own DB credentials (read from the
  # 0600 site_config.json) — no DB-root privilege needed, so it works for both
  # locally-created and externally-managed databases.
  mkSiteMigrate =
    name: siteCfg:
    let
      pkg = sitePackage siteCfg;
      pyEnv = pkgPythonEnv pkg;
      benchBin = "${pyEnv}/bin/bench";
      mg = cfg.migrate;
      runtimeBenchDir = "${siteCfg.siteDir}/bench";
      # pt-online-schema-change with its perl. See lib/offline-migrate.py.
      offlineMigrateTool = import ../lib/offline-migrate.nix { inherit pkgs; };

      dbName = siteCfg.database.name;
      renamedList = concatStringsSep ", " (mapAttrsToList (o: n: "${o} -> ${n}") siteCfg.renamedApps);
      # Connection flags shared by the guard, mysqldump (snapshot) and mysql
      # (rollback). Password comes from MYSQL_PWD (exported below) to keep it out
      # of argv. Connect the same way Frappe does: over the unix socket when one
      # is configured (locally-created DB users are `user@localhost`, which
      # MariaDB matches only for socket connections, not TCP to 127.0.0.1),
      # otherwise over TCP for an externally-managed database.
      connArgs =
        if siteCfg.database.socket != "" then
          "--socket=${siteCfg.database.socket} --user=${siteCfg.database.user}"
        else
          "--host=${siteCfg.database.host} --port=${toString siteCfg.database.port} --user=${siteCfg.database.user}";

      mysql = "${cfg.database.package}/bin/mysql";
      mysqldump = "${cfg.database.package}/bin/mysqldump";
      jq = "${pkgs.jq}/bin/jq";
      grep = "${pkgs.gnugrep}/bin/grep";
      gzip = "${pkgs.gzip}/bin/gzip";

      siteConfig = "${siteCfg.siteDir}/sites/${name}/site_config.json";
      # Build store path of the last successful migrate; guards against redundant
      # re-runs (e.g. on every reboot) when the app build hasn't changed.
      marker = "${siteCfg.siteDir}/.frappe-migrate-build";

      # Messages are this unit's: "frappe-migrate(<site>): …". Each step's stderr
      # is the journal, which reads a leading <N> as the line's syslog priority
      # (sd-daemon(3)); stdout lines are progress and take the default of info.
      say = msg: lib.escapeShellArg "frappe-migrate(${name}): ${msg}";

      # One step of the flow: a script with the site's environment.
      step =
        what: body:
        pkgs.writeShellScript "frappe-migrate-${what}-${name}" ''
          set -uo pipefail
          export PYTHONPATH="${pkgAppsPath pkg}"
          ${body}
        '';

      # The site's DB credentials, for the guard, the snapshot and the rollback
      # (the frappe user owns site_config.json).
      dbCredentials = ''
        MYSQL_PWD="$(${jq} -r '.db_password // empty' "${siteConfig}")"
        export MYSQL_PWD
      '';

      setMaintenance =
        state:
        optionalString mg.maintenanceMode ''
          ${benchBin} --site ${name} set-maintenance-mode ${state} \
            || echo "<4>"${say "warning: could not set maintenance mode ${state}"} >&2
        '';

      check = step "check" ''
        if [ "$(cat "${marker}" 2>/dev/null || true)" = "${pkg}" ]; then
          echo ${say "build unchanged since last successful migrate; skipping."}
          exit 100
        fi

        ${dbCredentials}
        # A database with no tables belongs to a site that has never been
        # installed — a fresh deploy awaiting `bench new-site`, or a host whose
        # local database state was lost while the site directory (on shared
        # storage) survived. `bench migrate` dies there on its very first query,
        #   Table '<db>.tabDefaultValue' doesn't exist
        # and takes activation down with it, every single deploy. Installing or
        # restoring a site is an operator action that activation cannot perform,
        # so this is not an activation failure: say what is missing and stop.
        # The marker stays unwritten, so the deploy after the restore migrates
        # even if the build has not changed.
        TABLES="$(${mysql} ${connArgs} -N -B -e \
          "SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA = '${dbName}';" 2>/dev/null)"
        if [ "$TABLES" = "0" ]; then
          echo "<4>"${say "database ${dbName} has no tables — the site is not installed; skipping migrate. Install (bench new-site) or restore it, then redeploy."} >&2
          exit 100
        fi
        exit 0
      '';

      dump = step "dump" ''
        set -e
        ${dbCredentials}
        ${mysqldump} ${connArgs} --single-transaction --quick --no-tablespaces \
          --routines --triggers ${dbName} | ${gzip}
      '';

      # Drop every current table and view — including any a partial migration
      # created — then re-import the snapshot (whose own DROP/CREATE/INSERT
      # restores the pre-migrate tables and data). FK checks off so drop order
      # does not matter. Both halves run whatever the first did, as before: a
      # load over a partial drop still puts back every table the dump has.
      restore = step "restore" ''
        ${dbCredentials}
        rc=0
        { echo "SET FOREIGN_KEY_CHECKS=0;"
          ${mysql} ${connArgs} -N -e \
            "SELECT CONCAT('DROP ', IF(TABLE_TYPE='VIEW','VIEW','TABLE'), ' IF EXISTS \`', TABLE_NAME, '\`;') FROM information_schema.TABLES WHERE TABLE_SCHEMA = '${dbName}';"
        } | ${mysql} ${connArgs} ${dbName} || rc=1
        ${gzip} -cd -- "$1" | ${mysql} ${connArgs} ${dbName} || rc=1
        exit "$rc"
      '';

      migrate = step "run" ''
        ${setMaintenance "on"}

        RC=0
        ${optionalString (siteCfg.renamedApps != { }) ''
          # Before anything imports the apps: the site still names OLD, which this
          # package no longer has. A failure here is a failed migrate.
          echo ${say "renaming ${renamedList}"}
          (cd ${runtimeBenchDir}/sites && ${pyEnv}/bin/python ${../lib/rename/frappe_rename_app.py} \
            --site ${name} --yes ${
              lib.escapeShellArgs (mapAttrsToList (o: n: "${o}=${n}") siteCfg.renamedApps)
            }) \
            || RC=$?
        ''}
        ${optionalString (siteCfg.replacedApps != { }) ''
          if [ "$RC" -eq 0 ]; then
            INSTALLED="$(${benchBin} --site ${name} list-apps --format json 2>/dev/null \
              | ${jq} -r --arg s ${name} '.[$s][]? // empty' 2>/dev/null)" || RC=$?
            for pair in ${lib.escapeShellArgs (mapAttrsToList (o: n: "${o}=${n}") siteCfg.replacedApps)}; do
              [ "$RC" -eq 0 ] || break
              old="''${pair%%=*}" new="''${pair#*=}"
              ${grep} -qxF "$old" <<< "$INSTALLED" || continue
              if ! ${grep} -qxF "$new" <<< "$INSTALLED"; then
                echo "frappe-migrate(${name}): $new replaces $old: installing $new"
                ${benchBin} --site ${name} install-app "$new" || RC=$?
              fi
              if [ "$RC" -eq 0 ]; then
                echo "frappe-migrate(${name}): $new replaces $old: uninstalling $old"
                ${benchBin} --site ${name} uninstall-app "$old" --yes --no-backup || RC=$?
              fi
            done
          fi
        ''}
        ${optionalString mg.offline.enable ''
          # A failure here is a failed migrate: the snapshot is restored, so a
          # half-applied plan is not left behind.
          if [ "$RC" -eq 0 ]; then
            echo ${say "altering large tables online"}
            FRAPPE_NIX_OFFLINE_MIGRATE_PT_OSC=${offlineMigrateTool}/bin/frappe-nix-pt-osc \
            FRAPPE_NIX_OFFLINE_MIGRATE_ROW_THRESHOLD=${toString mg.offline.rowThreshold} \
              ${pyEnv}/bin/python ${../lib/offline-migrate.py} --site ${name} --bench-root ${runtimeBenchDir} \
              || RC=$?
          fi
        ''}

        if [ "$RC" -eq 0 ]; then
          echo ${say "running bench migrate"}
          ${benchBin} --site ${name} migrate
          RC=$?
        fi
        exit "$RC"
      '';

      onSuccess = step "success" ''
        ${setMaintenance "off"}
        printf '%s' "${pkg}" > "${marker}"
      '';

      # Failure posture: maintenance mode stays ON (the migrate step turned it
      # on and nothing turned it off), so the site serves the maintenance page
      # instead of new code on the rolled-back (older) schema. Recover with a
      # fixed forward deploy or `nixos-rebuild switch --rollback`.
      onFailure = step "failure" (
        if mg.maintenanceMode then
          ''
            echo "<3>"${say "site left in maintenance mode; investigate and redeploy."} >&2
          ''
        else
          ''
            echo "<3>"${say "the site is serving on the schema the rollback left (migrate.maintenanceMode is off); investigate and redeploy."} >&2
          ''
      );
    in
    base.nixos.migrate.script {
      inherit
        pkgs
        check
        migrate
        onSuccess
        onFailure
        dump
        restore
        ;
      label = "frappe-migrate";
      target = name;
      cfg = mg;
      snapshotDir = "${siteCfg.siteDir}/snapshots";
      extension = ".sql.gz";
      verify = base.nixos.migrate.verifiers.mysqldump { inherit pkgs; };
      # The bench's own interpreter, already in the closure, when the package
      # names it (python-devops-nix PLAN.md, Python versions); a package built
      # by an older frappe-nix does not.
      python = pkg.passthru.python or pkgs.python3;
    };

  # Generate all systemd services for a single site.
  mkSiteServices =
    name: siteCfg:
    let
      pkg = sitePackage siteCfg;
      pyEnv = pkgPythonEnv pkg;
      node = pkgNodejs pkg;
      benchDir = pkgBenchDir pkg;
      runtimeBenchDir = "${siteCfg.siteDir}/bench";
      env = siteEnv name siteCfg;
      benchBin = "${pyEnv}/bin/bench";

      initName = "frappe-init-${name}";
      # Only locally-created DBs with a password go through the sync unit —
      # an externally-managed DB is the operator's responsibility.
      needsDbPasswordSync = siteCfg.database.createLocally && siteCfg.database.passwordFile != null;
      dbPasswordSyncName = "frappe-db-password-${name}";
      migrateName = "frappe-migrate-${name}";

      dependsOn = {
        after = [
          "${initName}.service"
          "${migrateName}.service"
        ];
        requires = [ "${initName}.service" ];
      };

      # workingDirectory defaults to the bench root because that is what the
      # `bench` CLI needs: bench locates its bench by walking cwd
      # (bench.utils.is_bench_directory(directory=os.path.curdir)), so
      # `bench schedule` / `bench worker` / `bench migrate` only work from
      # there. gunicorn is the exception -- see frappe-web in splitUnits. The
      # unified runtime is not: it starts at the bench root and changes into
      # sites/ itself before serving.
      mkService =
        {
          description,
          execStart,
          # APP_SERVICE, and with it the SyslogIdentifier; see logFields.
          role,
          identifier ? "frappe-${role}",
          extra ? { },
          workingDirectory ? runtimeBenchDir,
          stopTimeout ? null,
          # The unit's sandbox (see harden): what it writes, and the profiles
          # for what it runs.
          readWritePaths ? [ siteCfg.siteDir ],
          profiles ? frappeProfiles,
          # systemd BindPaths, as "source:destination" strings.
          bindPaths ? [ ],
        }:
        {
          inherit description;
          after = [ "network.target" ] ++ (extra.after or [ ]);
          requires = extra.requires or [ ];
          wantedBy = [ "multi-user.target" ];
          environment = env;
          path = servicePath;
          serviceConfig = {
            User = cfg.user;
            Group = cfg.group;
            WorkingDirectory = workingDirectory;
            ExecStart = execStart;
            Restart = "always";
            RestartSec = "5";
          }
          // environmentFileConfig
          // logFields {
            inherit role identifier;
            site = name;
          }
          // harden { inherit readWritePaths profiles; }
          // optionalAttrs (stopTimeout != null) {
            TimeoutStopSec = toString stopTimeout;
          }
          // optionalAttrs (bindPaths != [ ]) {
            BindPaths = bindPaths;
          };
        };

      workerUnits = lib.listToAttrs (
        map (
          queue:
          nameValuePair "frappe-worker-${queue}-${name}" (mkService {
            description = "Frappe worker (${queue}) for ${name}";
            role = "worker";
            # One identifier per queue: which queue a failing job ran on is the
            # first thing to know about it.
            identifier = "frappe-worker-${queue}";
            execStart = mkExec pkg "worker-${queue}-${name}" "${benchBin} worker --queue ${queue}";
            extra = dependsOn;
          })
        ) cfg.workers
      );

      # One process for the whole site: the web app, realtime, the jobs and the
      # scheduler. Listens where gunicorn used to, so nginx keeps one upstream and
      # /socket.io is just another location on it.
      runtimeUnits = {
        "frappe-${name}" = mkService {
          description = "Frappe runtime (web, realtime, jobs, scheduler) for ${name}";
          role = "runtime";
          bindPaths = [ (schedulerLockBind name runtimeBenchDir) ];
          execStart = mkExec pkg "runtime-${name}" (
            concatStringsSep " " (
              [ "${pyEnv}/bin/frappe-runtime" ]
              ++ (
                if siteCfg.web.socketPath != "" then
                  [
                    "--uds"
                    siteCfg.web.socketPath
                  ]
                else
                  [
                    "--host"
                    "0.0.0.0"
                    "--port"
                    (toString siteCfg.web.port)
                  ]
              )
              ++ [
                "--job-threads"
                (toString cfg.runtime.jobThreads)
                "--restart-after-requests"
                (toString cfg.runtime.restartAfterRequests)
                "--restart-after-jobs"
                (toString cfg.runtime.restartAfterJobs)
                "--restart-idle-seconds"
                (toString cfg.runtime.restartIdleSeconds)
                "--request-drain-seconds"
                (toString cfg.runtime.requestDrainSeconds)
                "--job-drain-seconds"
                (toString cfg.runtime.jobDrainSeconds)
              ]
              # Same queues the split workers took, as one comma-separated list.
              ++ lib.optionals (cfg.workers != [ ]) [
                "--queue"
                (concatStringsSep "," cfg.workers)
              ]
              ++ lib.optionals (cfg.runtime.webThreads != 0) [
                "--web-threads"
                (toString cfg.runtime.webThreads)
              ]
              ++ cfg.runtime.extraArgs
            )
          );
          extra = dependsOn;
          # The runner drains web requests and then background jobs on SIGTERM.
          # systemd's 90s default would SIGKILL it partway through, so give it the
          # whole window the runner was told to use, plus a margin for the final
          # ASGI lifespan shutdown.
          stopTimeout = cfg.runtime.requestDrainSeconds + cfg.runtime.jobDrainSeconds + 30;
        };
      };

      # gunicorn + node socket.io + scheduler + one unit per queue.
      splitUnits = {
        "frappe-web-${name}" = mkService {
          description = "Frappe web (gunicorn) for ${name}";
          role = "web";
          # The one service upstream does not run from the bench root: bench's
          # own supervisor.conf template gives frappe-web `directory={{ sites_dir }}`
          # and everything else `directory={{ bench_dir }}`, and bench runs frappe
          # subprocesses with `cwd=sites_dir` (bench.utils.run_frappe_cmd).
          #
          # It matters because frappe resolves some paths relative to cwd rather
          # than to SITES_PATH -- get_assets_json() is
          # frappe.read_file("assets/assets.json"). Running gunicorn from the
          # bench root made that miss, and since read_file() returns None for a
          # missing path and the result is cached in a shared Redis key with no
          # TTL, every rendered page 500d until the key was evicted (2026-09-09).
          #
          # This is not a substitute for the sites/assets link in mkSiteInit:
          # that covers the same relative form for the services which correctly
          # stay at the bench root.
          workingDirectory = "${runtimeBenchDir}/sites";
          execStart = mkExec pkg "web-${name}" ''
            ${pyEnv}/bin/gunicorn \
              --bind ${webBind siteCfg} \
              --workers ${toString cfg.web.workers} \
              --max-requests 5000 \
              --max-requests-jitter 500 \
              --timeout 120 \
              --preload \
              --graceful-timeout 30 \
              --keep-alive 5 \
              --access-logfile - \
              --error-logfile - \
              frappe.app:application'';
          extra = dependsOn;
        };

        "frappe-scheduler-${name}" = mkService {
          description = "Frappe scheduler for ${name}";
          role = "scheduler";
          bindPaths = [ (schedulerLockBind name runtimeBenchDir) ];
          execStart = mkExec pkg "scheduler-${name}" "${benchBin} schedule";
          extra = dependsOn;
          # It enqueues the scheduled jobs; the workers run them. Nothing here
          # starts Node or a PDF renderer.
          profiles = [ ];
        };

        "frappe-socketio-${name}" = mkService {
          description = "Frappe SocketIO for ${name}";
          role = "socketio";
          execStart = mkExec pkg "socketio-${name}" "${node}/bin/node ${benchDir}/apps/frappe/socketio.js";
          extra = dependsOn;
          # Node: V8 is a JIT. It reads the site's config and Redis and writes
          # nothing but its socket, in a /run directory (writable already).
          profiles = [ "jit" ];
          readWritePaths = [ ];
        };
      }
      // workerUnits;
    in
    {
      "${initName}" = {
        description = "Frappe init for site ${name}";
        wantedBy = [ "multi-user.target" ];
        after = lib.optional needsDbPasswordSync "${dbPasswordSyncName}.service";
        requires = lib.optional needsDbPasswordSync "${dbPasswordSyncName}.service";
        serviceConfig = {
          Type = "oneshot";
          RemainAfterExit = true;
          User = cfg.user;
          Group = cfg.group;
          # Source secret files (e.g. agenix's root:root 0400 outputs) are read
          # by systemd (root) and re-exposed under $CREDENTIALS_DIRECTORY owned
          # by cfg.user — the unit never needs direct read access to them.
          LoadCredential = map (s: "${s.key}:${s.file}") (mkSiteCredentials siteCfg);
          ExecStart = mkSiteInit name siteCfg;
        }
        // logFields {
          role = "init";
          site = name;
        }
        # It assembles the site's runtime tree and writes its site_config.json
        # with cp, ln and jq, and talks to nothing.
        // harden {
          readWritePaths = [ siteCfg.siteDir ];
          addressFamilies = unixOnly;
        };
      };

    }
    // (if cfg.runtime.enable then runtimeUnits else splitUnits)
    // optionalAttrs cfg.migrate.enable {
      "${migrateName}" = {
        description = "Frappe schema migration for ${name}";
        wantedBy = [ "multi-user.target" ];
        # Order after the data stores — migrate is a Restart-less oneshot and would
        # race MariaDB/Redis on a cold boot otherwise. Gate each on its createLocally
        # flag so this stays correct for externally-managed DB/Redis too.
        after = [
          "${initName}.service"
          "network.target"
        ]
        ++ lib.optional needsDbPasswordSync "${dbPasswordSyncName}.service"
        ++ lib.optional cfg.database.createLocally "mysql.service"
        ++ lib.optional cfg.redis.createLocally "redis-frappe.service";
        requires = [ "${initName}.service" ];
        environment = env;
        # coreutils for cat/printf used by the steps; mysql/mysqldump/jq/gzip
        # are referenced by absolute store path.
        path = servicePath ++ [ pkgs.coreutils ];
        serviceConfig = {
          Type = "oneshot";
          # RemainAfterExit=true keeps the unit active(exited) so switch-to-configuration
          # can restart it when the unit file changes (new pkgAppsPath store path on code change).
          RemainAfterExit = true;
          User = cfg.user;
          Group = cfg.group;
          WorkingDirectory = runtimeBenchDir;
          ExecStart = lib.getExe (mkSiteMigrate name siteCfg);
        }
        // environmentFileConfig
        // logFields {
          role = "migrate";
          site = name;
        }
        # Runs Frappe code, so the profiles of the units that do (see
        # frappeProfiles: after_migrate regenerates the Website Theme with
        # Node). The snapshots are under the site directory.
        // harden {
          readWritePaths = [ siteCfg.siteDir ];
          profiles = frappeProfiles;
          # Only unix sockets when everything it talks to is on one: the
          # database (its socket) and Redis (`bench migrate` clears the cache).
          addressFamilies =
            if
              siteCfg.database.socket != ""
              && lib.all (lib.hasPrefix "unix://") [
                siteCfg.redis.cacheUrl
                siteCfg.redis.queueUrl
                siteCfg.redis.socketioUrl
              ]
            then
              unixOnly
            else
              base.nixos.hardening.defaultAddressFamilies;
        };
      };
    }
    // optionalAttrs needsDbPasswordSync {
      "${dbPasswordSyncName}" = {
        description = "Sync MariaDB password for site ${name}";
        after = [ "mysql.service" ];
        requires = [ "mysql.service" ];
        serviceConfig = {
          Type = "oneshot";
          RemainAfterExit = true;
          # Must run as the MariaDB service's own system user — that's the
          # only unix_socket-mapped account with ALL PRIVILEGES, set up by
          # services.mysql's own postStart (see ensureUsers/ensureDatabases).
          User = config.services.mysql.user;
          LoadCredential = [ "db_password:${siteCfg.database.passwordFile}" ];
          ExecStart = mkSiteDbPasswordSync name siteCfg;
        }
        # Site setup like frappe-init, so the same role; its own identifier.
        // logFields {
          role = "init";
          site = name;
          identifier = "frappe-db-password";
        }
        # One statement to the local MariaDB over its socket; writes nothing.
        // harden { addressFamilies = unixOnly; };
      };
    };

  # bench CLI wrapper — defaults FRAPPE_SITE to the sole enabled site.
  # Uses the top-level package for interpreter discovery.
  siteNames = builtins.attrNames enabledSites;
  singleSite = if builtins.length siteNames == 1 then builtins.head siteNames else null;

  # What `bench` runs, as whoever runs it. benchCli below decides who that is.
  benchInner =
    let
      pkg = cfg.package;
      benchDir = pkgBenchDir pkg;
      pyEnv = pkgPythonEnv pkg;
      benchBin = "${pyEnv}/bin/bench";
      # extraEnv too: it is "for all Frappe services", and a `bench migrate` or
      # `bench execute` run by hand should see what the units see.
      coreEnvExports = concatStringsSep "\n" (
        mapAttrsToList (k: v: "export ${k}=${lib.escapeShellArg v}") (mkCoreEnv pkg // cfg.extraEnv)
      );
    in
    pkgs.writeShellScript "frappe-bench" ''
      set -euo pipefail

      # `bench restore` shells out to file(1) to sniff the archive type, and
      # `migrate` runs the bench's node by bare name. The wrapper runs under the
      # caller's PATH (a root shell has neither on a usable path for the frappe
      # user — node in particular can resolve to a directory it cannot execute,
      # which is the EACCES from website_theme), so put both on the front
      # rather than relying on the caller's PATH. wkhtmltopdf too, when it is
      # on, so a PDF rendered from here finds what the units do.
      export PATH="${
        lib.makeBinPath (
          [
            pkgs.file
            (pkgNodejs pkg)
          ]
          ++ wkhtmltopdfPath
        )
      }:$PATH"

      ${optionalString (singleSite != null) ''
        export FRAPPE_SITE=''${FRAPPE_SITE:-${singleSite}}
      ''}

      export PYTHONPATH="${pkgAppsPath pkg}"
      ${coreEnvExports}

      # Resolve SITES_PATH and runtime bench dir from the site's siteDir.
      FRAPPE_BENCH_ROOT=""
      ${concatStringsSep "\n" (
        mapAttrsToList (name: siteCfg: ''
          if [ "''${FRAPPE_SITE:-}" = "${name}" ]; then
            export SITES_PATH="${siteCfg.siteDir}/sites"
            FRAPPE_BENCH_ROOT="${siteCfg.siteDir}/bench"
          fi
        '') enabledSites
      )}
      export SITES_PATH=''${SITES_PATH:-/var/lib/frappe/sites}
      export FRAPPE_BENCH_ROOT=''${FRAPPE_BENCH_ROOT:-/var/lib/frappe/bench}

      # Ensure the mutable runtime bench tree exists (mirrors frappe-init).
      mkdir -p "$FRAPPE_BENCH_ROOT"/logs
      ln -sfn ${benchDir}/apps "$FRAPPE_BENCH_ROOT"/apps 2>/dev/null || true
      ln -sfn ${benchDir}/env  "$FRAPPE_BENCH_ROOT"/env  2>/dev/null || true
      ln -sfn "$SITES_PATH"    "$FRAPPE_BENCH_ROOT"/sites 2>/dev/null || true

      # config/ holds runtime state (scheduler lock/pid files), not just
      # static config — must be a real writable tree, same as frappe-init.
      mkdir -p "$FRAPPE_BENCH_ROOT"/config
      ${clearSchedulerLock ''"$FRAPPE_BENCH_ROOT"''}
      cp -rT ${benchDir}/config "$FRAPPE_BENCH_ROOT"/config
      chmod -R u+w "$FRAPPE_BENCH_ROOT"/config
      ${ensureSchedulerLock ''"$FRAPPE_BENCH_ROOT"''}

      # So a PDF rendered from here (`bench execute`, a print run by hand)
      # finds the same Chromium the units do.
      ${optionalString cfg.chromium.enable ''
        {
          ${linkChromium "$FRAPPE_BENCH_ROOT"}
        } 2>/dev/null || true
      ''}

      # The registry, but only where there is none yet (a `bench new-site`
      # before the first activation). frappe-init-<site> owns these links,
      # and this wrapper is built from the top-level package, which a site's
      # own `package` override may differ from — so never replace them here.
      {
        mkdir -p "$SITES_PATH"
        for f in apps.txt apps.json; do
          if [ ! -e "$SITES_PATH/$f" ] && [ -e "${benchDir}/sites/$f" ]; then
            ln -s "${benchDir}/sites/$f" "$SITES_PATH/$f"
          fi
        done
      } 2>/dev/null || true

      SITE_FLAG=""
      if [ -n "''${FRAPPE_SITE:-}" ]; then
        SITE_FLAG="--site $FRAPPE_SITE"
      fi

      cd "$FRAPPE_BENCH_ROOT"

      case "''${1:-}" in
        restore)
          shift
          if [ -z "''${1:-}" ]; then
            echo "Usage: bench restore <sql-file-path> [options]"
            exit 1
          fi
          SQL_FILE="$1"; shift
          exec ${benchBin} $SITE_FLAG restore "$SQL_FILE" "$@"
          ;;
        migrate|console|clear-cache)
          CMD="$1"; shift
          exec ${benchBin} $SITE_FLAG "$CMD" "$@"
          ;;
        new-site)
          # For a site declared here, the database and its user already exist:
          # ensureDatabases/ensureUsers made them for createLocally, and an
          # external database is the operator's, named by database.* and
          # already in site_config.json. Frappe's default setup would make
          # them again over a MariaDB root login, which a local MariaDB grants
          # only to OS root (unix_socket) and so refuses to whoever runs this.
          # So add --no-setup-db, the same way the dev shell's umbrella adds
          # its new-site flags, unless the caller passed any database flag of
          # Frappe's new-site (frappe/commands/site.py): then they asked for
          # something else, and get exactly what they asked for. --force is
          # never added: on an installed site it reinstalls the core tables.
          shift
          NEW_SITE=""
          DB_FLAGS=false
          SKIP=false
          for arg in "$@"; do
            if $SKIP; then SKIP=false; continue; fi
            case "$arg" in
              --db-*=* | --mariadb-*=*) DB_FLAGS=true ;;
              --setup-db | --no-setup-db | --no-mariadb-socket) DB_FLAGS=true ;;
              --db-* | --mariadb-*) DB_FLAGS=true; SKIP=true ;;
              --admin-password | --source-sql | --source_sql | --install-app) SKIP=true ;;
              -*) ;;
              *) if [ -z "$NEW_SITE" ]; then NEW_SITE="$arg"; fi ;;
            esac
          done
          SITE_CONFIG=""
          ${concatStringsSep "\n" (
            mapAttrsToList (name: siteCfg: ''
              if [ "$NEW_SITE" = ${lib.escapeShellArg name} ]; then
                SITE_CONFIG=${lib.escapeShellArg "${siteCfg.siteDir}/sites/${name}/site_config.json"}
              fi
            '') enabledSites
          )}
          if [ -n "$SITE_CONFIG" ] && ! $DB_FLAGS \
             && ${pkgs.jq}/bin/jq -e '.db_name and .db_password' "$SITE_CONFIG" >/dev/null 2>&1; then
            echo "bench: adding --no-setup-db: services.frappe-nix already provides $NEW_SITE's database and user (pass any --db-* flag to let Frappe set them up instead)" >&2
            exec ${benchBin} new-site --no-setup-db "$@"
          fi
          exec ${benchBin} new-site "$@"
          ;;
        *)
          exec ${benchBin} "$@"
          ;;
      esac
    '';

  # The `bench` an operator runs. From a root shell, frappe-bench above would
  # run as root, and everything it writes -- the runtime bench tree it
  # (re)creates, a site's public/private files, backups, logs -- would be
  # root's, which the units (running as cfg.user) then cannot write. So, as
  # root, it runs as a transient unit with the units' user, group, PATH and
  # environmentFiles (which systemd reads as root, as for the units). It is
  # not sandboxed like them: it is an operator's shell, and may need to reach
  # anything the operator points it at.
  # The site's secrets need nothing more: frappe-init-<site> has already
  # merged them into site_config.json, which is cfg.user's.
  #
  # --pty when attached to a terminal (`bench console`, `bench mariadb`),
  # --pipe otherwise, so stdin, stdout and stderr pass through as they are
  # (`bench mariadb < dump.sql`). Either way systemd-run waits and exits with
  # the command's status. FRAPPE_SITE is the one variable of the caller's that
  # is carried across, since it picks the site; TERM is carried by --pty.
  #
  # Anyone else (the service user, say) runs frappe-bench in place, as before.
  # The same as odoo-nix's adminCli.
  benchCli = pkgs.writeShellApplication {
    name = "bench";
    # For tests/pdf-renderers-unit.nix, which reads what frappe-bench does.
    passthru.inner = benchInner;
    text =
      optionalString (cfg.user != "root") ''
        if [ "$EUID" -eq 0 ]; then
          if [ -t 0 ] && [ -t 1 ]; then io=--pty; else io=--pipe; fi
          site=()
          if [ -n "''${FRAPPE_SITE:-}" ]; then site=(--setenv=FRAPPE_SITE="$FRAPPE_SITE"); fi
          exec ${config.systemd.package}/bin/systemd-run --quiet --wait --collect "$io" \
            --uid=${lib.escapeShellArg cfg.user} --gid=${lib.escapeShellArg cfg.group} \
            --setenv=PATH=${lib.escapeShellArg (lib.makeBinPath (servicePath ++ unitDefaultPath))} \
            ${
              concatStringsSep " " (
                map (f: "--property=${lib.escapeShellArg "EnvironmentFile=${f}"}") cfg.environmentFiles
              )
            } \
            "''${site[@]}" \
            -- ${benchInner} "$@"
        fi
      ''
      + ''
        exec ${benchInner} "$@"
      '';
  };

  # What NixOS puts on every unit's PATH after its own `path`
  # (systemd.services.<name>.path's defaults), which frappe-bench needs for
  # its own mkdir, cp and ln.
  unitDefaultPath = [
    pkgs.coreutils
    pkgs.findutils
    pkgs.gnugrep
    pkgs.gnused
    config.systemd.package
  ];

  # Where gunicorn listens: a unix socket when web.socketPath is set, else TCP.
  webBind =
    siteCfg:
    if siteCfg.web.socketPath != "" then
      "unix:${siteCfg.web.socketPath}"
    else
      "0.0.0.0:${toString siteCfg.web.port}";

  # nginx upstream names are used as a host in proxy_pass, so a site's FQDN has
  # to be flattened to keep it unambiguous.
  upstreamName = name: "frappe-web-${lib.replaceStrings [ "." ] [ "_" ] name}";
  socketioUpstreamName = name: "frappe-socketio-${lib.replaceStrings [ "." ] [ "_" ] name}";

  # Per-site nginx virtualHost config.
  mkSiteNginxVhost =
    name: siteCfg:
    let
      viaSocket = siteCfg.nginx.socketPath != "";
      webUpstream =
        if siteCfg.web.socketPath != "" then
          "http://${upstreamName name}"
        else
          "http://127.0.0.1:${toString siteCfg.web.port}";

      # Under the unified runtime one process answers both, so /socket.io points at
      # the web upstream and there is no separate socketio upstream at all.
      socketioUpstream =
        if cfg.runtime.enable then
          webUpstream
        else if siteCfg.socketio.socketPath != "" then
          "http://${socketioUpstreamName name}"
        else
          "http://127.0.0.1:${toString siteCfg.socketio.port}";

      # Over a unix socket $scheme is "http", but TLS was terminated at the edge
      # in front of us, so the public scheme is https.
      pubScheme = if viaSocket then "https" else "$scheme";

      # recommendedProxySettings hardcodes `X-Forwarded-Proto $scheme` and NixOS
      # emits its include AFTER a location's extraConfig, so the wrong scheme
      # cannot be overridden from there. Opt the location out and set the whole
      # header block explicitly instead (python-devops-nix's lib.nixos.nginx).
      socketProxyHeaders = optionalString viaSocket base.nixos.nginx.socketProxyHeaders;
    in
    {
      root = "${siteCfg.siteDir}/sites";

      # Socket mode listens on the unix socket for the proxy or tunnel in front.
      #
      # It used to also listen on loopback:80, because node's realtime server
      # validated sessions by making an HTTP request back to this site's own FQDN
      # (pinned to 127.0.0.1 in networking.hosts below) and node's fetch cannot
      # speak unix. The Python runtime validates in-process against the WSGI app,
      # so under runtime.enable that hop -- and the host pin -- are gone.
      listen = lib.optionals viaSocket (
        [ { addr = "unix:${siteCfg.nginx.socketPath}"; } ]
        ++ lib.optional (!cfg.runtime.enable) {
          addr = "127.0.0.1";
          port = 80;
        }
      );

      # A unix socket has no peer address, so $remote_addr is meaningless and
      # recommendedProxySettings would forward it as the client IP. Trust the
      # socket peer and take the real address from Cloudflare's header instead —
      # the socket is only reachable from the connector beside it.
      extraConfig = lib.optionalString viaSocket base.nixos.nginx.cloudflareRealIp;

      locations = {
        "/assets/" = {
          extraConfig = ''
            try_files $uri =404;
            add_header Cache-Control "max-age=31536000";
          '';
        };
        # The runtime does not serve uploads, so nginx answers /files/ from the
        # site's public directory, as bench's nginx template does. Markup types
        # are forced to download so an uploaded page or SVG cannot run script
        # on the site's origin.
        "/files/" = {
          extraConfig = ''
            try_files /${name}/public$uri =404;
          '';
        };
        "~* ^/files/.*\\.(htm|html|svg|xml)$" = {
          extraConfig = ''
            add_header Content-Disposition "attachment";
            try_files /${name}/public$uri =404;
          '';
        };
        # Frappe checks a private file's permissions, then hands the transfer
        # back to nginx with X-Accel-Redirect: /protected/<path under the site>.
        "~ ^/protected/(.*)" = {
          extraConfig = ''
            internal;
            try_files /${name}/$1 =404;
          '';
        };
        "/socket.io" = {
          proxyPass = socketioUpstream;
          proxyWebsockets = true;
          recommendedProxySettings = !viaSocket;
          extraConfig = ''
            ${socketProxyHeaders}
            proxy_set_header X-Frappe-Site-Name ${name};
            proxy_set_header Origin ${pubScheme}://$http_host;
          '';
        };
        "/" = {
          proxyPass = webUpstream;
          recommendedProxySettings = !viaSocket;
          extraConfig = ''
            ${socketProxyHeaders}
            proxy_set_header X-Frappe-Site-Name ${name};
            proxy_set_header X-Use-X-Accel-Redirect True;
          '';
        };
      };
    };

  # sites.<name>.<from> renamed to <to>, inside the site submodule. Not
  # lib.mkRenamedOptionModule: a submodule has no `warnings` to report through,
  # so doRename does the aliasing and the warning goes on the site's
  # `deprecations`, which the module's top level turns into `warnings`.
  # Removed in frappe-nix 2.0.0, never within 1.x.
  renamedSiteOption =
    from: to:
    { name, options, ... }:
    {
      imports = [
        (lib.doRename {
          from = [ from ];
          to = [ to ];
          visible = false;
          warn = false;
          use = lib.id;
        })
      ];
      config.deprecations =
        lib.optional options.${from}.isDefined
          "The option `services.frappe-nix.sites.\"${name}\".${from}' defined in ${
            lib.showFiles options.${from}.files
          } has been renamed to `services.frappe-nix.sites.\"${name}\".${to}'. The old name works through frappe-nix 1.x and goes in 2.0.0.";
    };

  # A site's socket paths, for lib.nixos.unixSocket. nginx *creates* its listen
  # socket and is only a member of cfg.group, so its directory is 0770 (group
  # write); a directory holding only the site's own sockets, which nginx only
  # connects to, is 0750. Both of a site's sockets normally share one directory,
  # which is then 0770.
  siteSockets =
    name: siteCfg:
    let
      option = opt: "services.frappe-nix.sites.\"${name}\".${opt}";
      example = "/run/frappe-${name}/web.sock";
    in
    [
      {
        option = option "web.socketPath";
        path = siteCfg.web.socketPath;
        inherit example;
      }
      {
        option = option "socketio.socketPath";
        path = siteCfg.socketio.socketPath;
        inherit example;
      }
      {
        option = option "nginx.socketPath";
        path = siteCfg.nginx.socketPath;
        peerCreates = true;
        inherit example;
      }
    ];

  # The directories, deduplicated across sites. The realtime socket only under
  # the split units: the unified runtime binds none (a set socketio.socketPath
  # is a warning there).
  socketDirs = base.nixos.unixSocket {
    inherit (cfg) user group;
    sockets = lib.concatLists (
      mapAttrsToList (
        name: siteCfg:
        lib.filter (socket: !(cfg.runtime.enable && lib.hasSuffix ".socketio.socketPath" socket.option)) (
          siteSockets name siteCfg
        )
      ) enabledSites
    );
  };

  # Site submodule option definition.
  siteModule = types.submodule (
    { name, ... }: {
      # python-devops-nix CONVENTIONS.md, Framework config blocks.
      imports = [
        (renamedSiteOption "extraConfig" "settings")
        (renamedSiteOption "extraConfigFiles" "settingsFiles")
      ];

      options = {
        enable = mkEnableOption "this Frappe site";

        package = mkOption {
          type = types.nullOr types.package;
          default = null;
          description = "Per-site bench package override. Defaults to services.frappe-nix.package.";
        };

        siteDir = mkOption {
          type = types.str;
          default = "/var/lib/frappe/${name}";
          description = "State directory for this site.";
        };

        web.port = mkOption {
          type = types.port;
          default = 8000;
          description = "Gunicorn listen port for this site (ignored when web.socketPath is set).";
        };

        web.socketPath = mkOption {
          type = types.str;
          default = "";
          example = "/run/frappe-erp/web.sock";
          description = ''
            Bind gunicorn to this unix socket instead of a TCP port, removing the
            nginx->gunicorn hop from the network stack. nginx reaches it through a
            generated upstream block.

            Access is governed by the socket's *directory*, which this module
            creates owned by the service user and group — nginx is already a member
            of that group: 0750, or 0770 when nginx.socketPath shares it. Put the
            socket in its own directory, not directly in /run.
          '';
        };

        socketio.port = mkOption {
          type = types.port;
          default = 9000;
          description = "SocketIO listen port for this site (ignored when socketio.socketPath is set).";
        };

        socketio.socketPath = mkOption {
          type = types.str;
          default = "";
          example = "/run/frappe-mysite/socketio.sock";
          description = ''
            Unix socket for the realtime server, the counterpart of web.socketPath.
            When set, socketio.port is unused and the site has no TCP listener on
            9000 at all — nginx reaches it through a generated upstream block.

            Frappe reads this as `socketio_uds`; support landed in v15.46 and every
            v16, so an older bench must leave this empty.

            The same directory rules as web.socketPath apply: access is governed by
            the socket's directory, so give it its own.

            Only used when services.frappe-nix.runtime.enable is false. The unified
            runtime serves /socket.io from the same process — and the same socket —
            as the web app, so it has no separate realtime listener.

            In that legacy mode this does not remove nginx's loopback :80 listener.
            That is there because the node realtime server validates sessions by
            making an HTTP request back to the site's own FQDN, and node's fetch
            cannot speak unix — so the callback still needs a TCP way in.
          '';
        };

        database = {
          createLocally = mkEnableOption "a local MariaDB database for this site";
          host = mkOption {
            type = types.str;
            default = "127.0.0.1";
          };
          port = mkOption {
            type = types.port;
            default = 3306;
          };
          socket = mkOption {
            type = types.str;
            default = "/run/mysqld/mysqld.sock";
            description = "Database unix socket (empty to disable socket auth).";
          };
          name = mkOption {
            type = types.str;
            default = builtins.replaceStrings [ "." "-" ] [ "_" "_" ] name;
            description = "Database name. Defaults to site name with dots/hyphens replaced by underscores.";
          };
          user = mkOption {
            type = types.str;
            default = builtins.replaceStrings [ "." "-" ] [ "_" "_" ] name;
            description = "Database user. Defaults to site name with dots/hyphens replaced by underscores.";
          };
          passwordFile = mkOption {
            type = types.nullOr types.path;
            default = null;
            description = "File containing the database password. Merged into site_config.json at activation.";
          };
        };

        redis = {
          cacheUrl = mkOption {
            type = types.str;
            default = "redis://127.0.0.1:13000";
          };
          queueUrl = mkOption {
            type = types.str;
            default = "redis://127.0.0.1:13000";
          };
          socketioUrl = mkOption {
            type = types.str;
            default = "redis://127.0.0.1:13000";
          };
        };

        encryptionKeyFile = mkOption {
          type = types.nullOr types.path;
          default = null;
          description = "File containing the Frappe encryption key. Merged into site_config.json at activation.";
        };

        settings = mkOption {
          type = types.attrsOf types.anything;
          default = { };
          example = lib.literalExpression ''{ host_name = "https://erp.example.com"; monitor = 1; }'';
          description = ''
            Keys merged into the site's site_config.json, under Frappe's own
            spelling (Nix values, no secrets: they land in the store). Was
            `extraConfig`.
          '';
        };

        settingsFiles = mkOption {
          type = types.listOf types.path;
          default = [ ];
          description = ''
            JSON files deep-merged into site_config.json at activation, for
            secrets: each is read through LoadCredential, never copied to the
            store. Was `extraConfigFiles`.
          '';
        };

        deprecations = mkOption {
          type = types.listOf types.str;
          default = [ ];
          internal = true;
          description = "Renamed options this site sets, each a warning at the top level.";
        };

        renamedApps = mkOption {
          type = appPairs;
          default = { };
          example = lib.literalExpression ''{ esign = "esign_webforms"; }'';
          description = ''
            Apps renamed in place, `OLD = NEW` (`frappe-rename-app`; see
            docs/app-standards/rename.md). The package carries NEW only, and
            `bench migrate` fails on an installed app it cannot import, so the
            migrate unit runs `frappe-rename-app --site` on this site right
            after maintenance mode goes on and before the offline migrate and
            `bench migrate`, inside the same snapshot and rollback. A no-op once
            the site names NEW. Pause the scheduler and drain the job queues
            before the deploy that ships the rename.
          '';
        };

        replacedApps = mkOption {
          type = appPairs;
          default = { };
          example = lib.literalExpression ''{ old_app = "new_app"; }'';
          description = ''
            Apps replaced by a new app, `OLD = NEW` (spec §5.10): both are in the
            package. On a site with OLD installed, the migrate unit installs NEW
            (if it is not yet) and uninstalls OLD, without a backup of its own
            (the pre-migrate snapshot is the backup), after maintenance mode goes
            on and before migrating, inside the same snapshot and rollback. A
            no-op where OLD is not installed. Drop OLD from the package only
            after every site has run this: the uninstall needs its hooks.
          '';
        };

        nginx = {
          enable = mkEnableOption "an nginx virtualHost for this site";

          socketPath = mkOption {
            type = types.str;
            default = "";
            example = "/run/frappe-erp/nginx.sock";
            description = ''
              Additionally serve this vhost over a unix socket, for a co-located
              reverse proxy or tunnel connector that terminates TLS elsewhere.

              The loopback :80 listener is kept alongside it — the socketio
              session-validation callback resolves the site FQDN to 127.0.0.1 and
              needs it. In this mode the client IP comes from `CF-Connecting-IP`,
              since a unix socket has no peer address.

              nginx chmods its unix listen sockets to 0666, so the socket file does
              not restrict access. Give it its own directory; this module creates
              that owned by the service user and group, 0770 since nginx creates
              its socket there as a group member, which is the real gate.
            '';
          };
        };
      };
    }
  );

in
{
  # The module was services.frappe. It matches the devenv and flake-parts roots
  # (frappe-nix.*) now, and cannot collide with a future nixpkgs module
  # (python-devops-nix CONVENTIONS.md, NixOS module path). The whole tree is
  # aliased, with NixOS's own warning, through 1.x, until frappe-nix 2.0.0.
  #
  # lib.mkRenamedOptionModule, but withPriority = false: its priority wrapper
  # puts a whole tree's definitions under one mkOverride, which leaves a nested
  # mkIf (`runtime.webThreads = mkIf …`) undischarged at the leaf. Without it
  # each leaf keeps its own priority and properties.
  imports = [
    (lib.doRename {
      from = [
        "services"
        "frappe"
      ];
      to = [
        "services"
        "frappe-nix"
      ];
      visible = false;
      warn = true;
      use = lib.trace "Obsolete option `services.frappe' is used. It was renamed to `services.frappe-nix', and the old name goes in frappe-nix 2.0.0.";
      withPriority = false;
    })
  ];

  options.services.frappe-nix = {
    enable = mkEnableOption "Frappe bench production deployment (systemd)";

    package = mkOption {
      type = types.package;
      description = "Default bench package (builtBench). Sites inherit this unless they set their own.";
    };

    user = mkOption {
      type = types.str;
      default = "frappe";
    };

    group = mkOption {
      type = types.str;
      default = "frappe";
    };

    web.workers = mkOption {
      type = types.int;
      default = 4;
      description = "Number of gunicorn workers (shared across sites).";
    };

    workers = mkOption {
      type = types.listOf types.str;
      default = [
        "default"
        "short"
        "long"
      ];
      description = ''
        Background worker queues to run per site. Under the unified runtime these
        are passed to the runner as --queue instead of becoming one unit each.
      '';
    };

    # The unified Python runtime (github:Avunu/frappe-runtime): one process per
    # site serving the web app, realtime, the background jobs and the scheduler,
    # in place of gunicorn + node socket.io + N workers + the scheduler.
    #
    # It also removes the reason nginx needed a loopback :80 listener and a
    # networking.hosts pin: realtime validates sessions in-process against the
    # WSGI app rather than making an HTTP request back to the site's own FQDN.
    runtime = {
      enable = mkOption {
        type = types.bool;
        default = true;
        description = ''
          Run each site as a single frappe-runtime process instead of separate
          gunicorn, node socket.io, worker and scheduler units.

          Requires the frappe-runtime package in the bench's Python environment;
          see the frappe-nix README for the uv.lock entry. Set false to keep the
          split units and the Node realtime server.
        '';
      };

      jobThreads = mkOption {
        type = types.int;
        default = 4;
        description = "Concurrent background jobs inside the runtime process.";
      };

      webThreads = mkOption {
        type = types.int;
        default = 0;
        description = ''
          Concurrent web requests. 0 leaves the default of frappe_runtime.asgi
          (FRAPPE_WEB_THREADS, itself defaulting to 8). Size the database pool
          against whatever this ends up being.
        '';
      };

      restartAfterRequests = mkOption {
        type = types.int;
        default = 5000;
        description = "Web requests before a graceful restart (0 = never).";
      };

      restartAfterJobs = mkOption {
        type = types.int;
        default = 500;
        description = "Background jobs before a graceful restart (0 = never).";
      };

      restartIdleSeconds = mkOption {
        type = types.int;
        default = 300;
        description = "Idle time before a graceful restart (0 = never).";
      };

      # These are options rather than extraArgs because the unit's TimeoutStopSec
      # is derived from them. systemd's built-in default is 90s; the runner's own
      # default job drain is 600s, so left alone systemd SIGKILLs the process
      # mid-drain and the graceful shutdown this runtime exists for never
      # completes.
      requestDrainSeconds = mkOption {
        type = types.int;
        default = 60;
        description = "How long a graceful stop waits for in-flight web requests.";
      };

      jobDrainSeconds = mkOption {
        type = types.int;
        default = 600;
        description = ''
          How long a graceful stop waits for a background job in progress.

          TimeoutStopSec is derived from this, so raising it also gives systemd
          the patience to let the drain finish.
        '';
      };

      extraArgs = mkOption {
        type = types.listOf types.str;
        default = [ ];
        example = [
          "--request-drain-seconds"
          "120"
        ];
        description = "Extra arguments appended to the frappe-runtime command line.";
      };
    };

    database = {
      createLocally = mkEnableOption "a local MariaDB instance (aggregate: enabled if any site requests it)";
      package = mkOption {
        type = types.package;
        default = pkgs.mariadb;
        description = "MariaDB package (client library on LD_LIBRARY_PATH).";
      };
      slowQueryMs = mkOption {
        type = types.nullOr types.ints.unsigned;
        default = null;
        example = 500;
        description = ''
          Log every statement that runs at least this many milliseconds to
          MariaDB's slow query log (`slow_query_log`, `long_query_time`); `0`
          logs all of them. MariaDB writes it to a file, by default
          `<hostname>-slow.log` in its data directory (`slow_query_log_file`
          moves it), not to the journal, and nothing rotates it. Only with a
          local database (`database.createLocally`). The same option as
          odoo-nix's, whose PostgreSQL logs to the journal.
        '';
      };
    };

    redis = {
      createLocally = mkEnableOption "a local Redis instance for Frappe";
      port = mkOption {
        type = types.port;
        default = 13000;
      };
    };

    # The four options odoo-nix has under the same names and defaults
    # (python-devops-nix's lib.nixos.migrate.options), and this module's own
    # beside them.
    migrate =
      base.nixos.migrate.options {
        what = "`bench migrate` for each site (the per-site frappe-migrate-<site> oneshot)";
        dumpTool = "mysqldump";
        where = "per site, under <siteDir>/snapshots";
      }
      // {
        maintenanceMode = mkOption {
          type = types.bool;
          default = true;
          description = ''
            Put the site into Frappe maintenance mode around the migration. On
            failure the site is left in maintenance mode so it serves the
            maintenance page rather than running new code against a rolled-back
            (older) schema.
          '';
        };
        offline = {
          enable = mkOption {
            type = types.bool;
            default = false;
            description = ''
              Alter large tables without locking them. Before `bench migrate`, the
              unit works out which tables of at least `rowThreshold` rows the
              migrate is about to alter and applies those changes with
              `pt-online-schema-change` — a shadow copy altered and filled in
              chunks while triggers keep it current, swapped in with one rename —
              so the ALTER does not hold a metadata lock for the length of the
              copy. The migrate that follows finds the columns already there.

              Runs inside the same snapshot, maintenance mode and rollback as the
              migrate, and a failure is a failed migrate. Off by default: it adds
              percona-toolkit to the closure and needs the TRIGGER privilege on
              the site's database, which a locally created site user has.
            '';
          };
          rowThreshold = mkOption {
            type = types.ints.unsigned;
            default = 100000;
            description = ''
              Row count at which a table is altered online rather than by
              `bench migrate` itself. `0` sends every table with a pending change
              through pt-online-schema-change.
            '';
          };
        };
      };

    # Everything goes to the journal; nothing here writes a log file. Every unit
    # is labelled with APP_SERVICE (and APP_SITE where it serves one site) — see
    # logFields — and the bench's virtualenv carries frappe_journald, which gives
    # each Python log line its syslog priority. These two options are the knobs.
    logging = {
      level = mkOption {
        type = types.enum [
          "debug"
          "info"
          "warning"
          "error"
        ];
        default = "warning";
        description = ''
          Threshold for Frappe's application loggers (`frappe.logger()`) and for
          bench's own log, passed as FRAPPE_NIX_LOG_LEVEL.

          Frappe's own production default is error, which silently drops every
          `frappe.logger().warning()`; warning keeps those at no real cost in
          volume. An explicit `frappe.utils.logger.set_log_level()` still wins.

          The runtime's lifecycle lines (restarts, drains) are logged at info
          regardless, and gunicorn, Node, MariaDB and Redis keep their own levels.
        '';
      };

      accessLog = mkOption {
        type = types.bool;
        default = true;
        description = ''
          Send nginx's HTTP access log to the journal as one JSON object per
          request (SYSLOG_IDENTIFIER=nginx_access, fields time, site, method, uri,
          status, bytes, request_time, upstream_time, remote_addr, user_agent,
          referer). false turns access logging off altogether.

          Either way nginx no longer writes /var/log/nginx/access.log. Set at the
          http level, so it applies to every virtualHost on the host, not only
          the Frappe sites.
        '';
      };
    };

    chromium = {
      enable = mkEnableOption ''
        Chromium for PDFs: print formats on Frappe's "chrome" PDF generator,
        and Print Designer's. Each site's runtime bench gets a NixOS build of
        chrome-headless-shell at chromium/chrome-linux/headless_shell, where
        both look for it, so neither downloads one of its own (which would not
        run on NixOS). A `chromium_path` in common_site_config.json still takes
        precedence.

        Also adds fonts.packages for the pages to render with: Liberation
        (metric-compatible with Arial, Times New Roman and Courier New) and
        Noto. Without any fonts a page renders with no text at all. Off by
        default because the browser and the fonts add up to about 0.5 GiB to
        the closure'';

      package = mkOption {
        type = types.package;
        default = chromeHeadlessShell;
        defaultText = lib.literalMD "chrome-headless-shell from `pkgs.playwright-driver.components.chromium-headless-shell`";
        description = ''
          The Chromium the sites render PDFs with. Its main program is what gets
          linked.

          It should be a chrome-headless-shell, which is what Frappe downloads
          and expects. Full Chromium (`pkgs.chromium`) starts in a different
          headless mode that needs a writable home directory, which the
          `frappe` user does not have, and leaves a profile behind for every
          PDF.
        '';
      };
    };

    wkhtmltopdf = {
      enable = mkEnableOption ''
        wkhtmltopdf for PDFs: Frappe's "wkhtmltopdf" PDF generator, which is
        what the Standard print and every classic (HTML) print format render
        with unless they name another. Print-builder formats use Chromium or
        Typst instead. Puts it on the units' PATH, with the `which` that pdfkit
        finds it through, and adds the same fonts as chromium.enable. Off by
        default because it adds about 200 MiB to the closure'';

      package = mkOption {
        type = types.package;
        default = pkgs.wkhtmltopdf;
        defaultText = lib.literalExpression "pkgs.wkhtmltopdf";
        description = ''
          The wkhtmltopdf the sites render PDFs with. Frappe's headers and
          footers need a build "with patched qt", as nixpkgs' is.
        '';
      };
    };

    hardening.enable = mkOption {
      type = types.bool;
      default = false;
      description = ''
        Sandbox every frappe-* unit with python-devops-nix's
        lib.nixos.hardening: the file system read-only except the site's
        directory, no capabilities, a system call filter, private /tmp, and no
        writable-and-executable memory except for the units that run Node or a
        PDF renderer. Off, the units get none of it.

        The default flips to true in frappe-nix 2.0.0. Enable it per
        deployment after watching one start, especially inside NixOS
        containers (systemd-nspawn), where a namespace the sandbox needs can
        fail the unit with status 226/NAMESPACE.
      '';
    };

    extraEnv = mkOption {
      type = types.attrsOf types.str;
      default = { };
      description = "Additional environment variables for all Frappe services.";
    };

    environmentFiles = mkOption {
      type = types.listOf types.path;
      default = [ ];
      example = lib.literalExpression "[ config.age.secrets.frappe-env.path ]";
      description = ''
        Files of `KEY=value` lines handed to every unit that runs Frappe code
        (the runtime, or gunicorn, the workers, the scheduler and socket.io,
        and migrate) as systemd `EnvironmentFile=`s, and to `bench` run from a
        root shell, so secrets an app reads from its environment never enter
        the Nix store. systemd reads them as root when the unit starts, so an
        agenix secret's root-only mode is fine. The same shape as odoo-nix's
        `services.odoo-nix.environmentFiles`.

        For secrets that belong in site_config.json, use
        `sites.<name>.settingsFiles` instead.
      '';
    };

    extraPath = mkOption {
      type = types.listOf types.package;
      default = [ ];
      description = ''
        Additional packages on PATH for all Frappe services (web, workers,
        migrate). Needed because systemd's `path` sets PATH to exactly the
        listed packages' bin/sbin, not falling back to
        /run/current-system/sw/bin — a package only in
        environment.systemPackages is otherwise invisible to these services
        even though it's installed system-wide.

        Only for packages specific to your deployment. What Frappe itself
        resolves at runtime is already on the services' PATH — git, plus the
        backup toolchain (gzip, tar, bash, mariadb-dump); see servicePath.
        The one Frappe-side exception is gnupg, needed only when
        conf.encrypt_backup is set.
      '';
    };

    sites = mkOption {
      type = types.attrsOf siteModule;
      default = { };
      description = "Per-site configuration. Each key is the site name (FQDN).";
    };
  };

  config = mkIf cfg.enable (mkMerge [
    {
      # Socket paths must sit in their own directory: nginx chmods its unix listen
      # sockets to 0666 and gunicorn's mode follows its umask, so neither socket
      # file gates access. The directory this module creates around it does
      # (python-devops-nix's lib.nixos.unixSocket; see socketDirs).
      assertions =
        map (socket: base.nixos.unixSocket.assertion (removeAttrs socket [ "peerCreates" ])) (
          lib.concatLists (mapAttrsToList siteSockets enabledSites)
        )
        ++ lib.concatLists (
          mapAttrsToList (
            name: siteCfg:
            lib.optional (siteCfg.nginx.socketPath != "" && !siteCfg.nginx.enable) {
              assertion = false;
              message = "services.frappe-nix.sites.\"${name}\".nginx.socketPath requires nginx.enable.";
            }
          ) enabledSites
        );

      # The unified runtime has no separate realtime process and no gunicorn, so
      # these configure nothing. Say so rather than letting a set value quietly
      # do nothing.
      warnings =
        lib.concatMap (siteCfg: siteCfg.deprecations) (lib.attrValues cfg.sites)
        ++ lib.optionals cfg.runtime.enable (
          lib.concatLists (
            mapAttrsToList (
              name: siteCfg:
              lib.optional (siteCfg.socketio.socketPath != "") (
                "services.frappe-nix.sites.\"${name}\".socketio.socketPath is ignored when"
                + " services.frappe-nix.runtime.enable is true: the runtime serves /socket.io"
                + " on web.socketPath. Remove it, or set runtime.enable = false."
              )
            ) enabledSites
          )
          ++ lib.optional (cfg.web.workers != 4) (
            "services.frappe-nix.web.workers is ignored when services.frappe-nix.runtime.enable"
            + " is true: the runtime sizes web concurrency with"
            + " services.frappe-nix.runtime.webThreads instead."
          )
        );

      environment.systemPackages = [
        benchCli
        pkgs.git
      ]
      ++ (cfg.package.passthru.extraPackages or [ ]);

      users.users = mkIf (cfg.user == "frappe") {
        frappe = {
          isSystemUser = true;
          inherit (cfg) group;
          home = "/var/lib/frappe";
          description = "Frappe service user";
        };
      };
      users.groups = mkIf (cfg.group == "frappe") {
        frappe = { };
      };

      # Generate per-site systemd services.
      systemd.services = lib.mkMerge (
        mapAttrsToList (name: siteCfg: mkSiteServices name siteCfg) enabledSites
      );

      # Per-site tmpfiles rules to ensure siteDir exists with correct ownership.
      systemd.tmpfiles.rules =
        mapAttrsToList (_name: siteCfg: "d ${siteCfg.siteDir} 0750 ${cfg.user} ${cfg.group} -") enabledSites
        # The file too, not just its directory: it is the BindPaths source of
        # the scheduler units, and systemd refuses to start on a missing one.
        ++ lib.concatMap (name: [
          "d ${schedulerLockDir name} 0750 ${cfg.user} ${cfg.group} -"
          "f ${schedulerLockDir name}/scheduler_process 0640 ${cfg.user} ${cfg.group} -"
        ]) (builtins.attrNames enabledSites)
        # Socket directories gate access to the sockets inside them, since nginx
        # chmods its listen socket to 0666 and gunicorn's follows its umask.
        ++ socketDirs.tmpfiles;
    }

    # Aggregate database.createLocally: enable MariaDB if any site requests it
    # or if the top-level toggle is on.
    (mkIf
      (
        cfg.database.createLocally
        || lib.any (s: s.database.createLocally) (builtins.attrValues enabledSites)
      )
      {
        services.mysql = {
          enable = true;
          package = cfg.database.package;
          # `ensureUsers` only creates passwordless unix_socket accounts —
          # the actual password is set separately by mkSiteDbPasswordSync,
          # since NixOS deliberately doesn't manage passwords declaratively.
          ensureDatabases = mapAttrsToList (_: s: s.database.name) (
            filterAttrs (_: s: s.database.createLocally) enabledSites
          );
          ensureUsers = mapAttrsToList (_: s: {
            name = s.database.user;
            ensurePermissions = {
              "${s.database.name}.*" = "ALL PRIVILEGES";
            };
          }) (filterAttrs (_: s: s.database.createLocally) enabledSites);
          settings.mysqld = {
            character-set-server = "utf8mb4";
            collation-server = "utf8mb4_unicode_ci";
            skip-character-set-client-handshake = true;
            innodb-read-only-compressed = "OFF";
          }
          // optionalAttrs (cfg.database.slowQueryMs != null) {
            slow_query_log = 1;
            # Seconds, to the microsecond; written out as a string so the
            # ini format never sees a float.
            long_query_time =
              let
                ms = cfg.database.slowQueryMs;
                frac = toString (lib.mod ms 1000);
              in
              "${toString (ms / 1000)}.${lib.fixedWidthString 3 "0" frac}";
          };
        };

        # Shared by every site, so no APP_SITE.
        systemd.services.mysql.serviceConfig = logFields { role = "db"; };
      }
    )

    # A server has no fonts unless something asks for them, and Chromium renders
    # text it has no font for as nothing at all. These are the fonts the OCI
    # images carry (modules/containers.nix). fonts.packages is a list, so these
    # add to an operator's own rather than replacing them.
    (mkIf (cfg.chromium.enable || cfg.wkhtmltopdf.enable) {
      fonts.packages = [
        pkgs.liberation_ttf
        pkgs.noto-fonts
      ];
    })

    (mkIf cfg.redis.createLocally {
      services.redis.servers.frappe = {
        enable = true;
        port = cfg.redis.port;
        bind = "127.0.0.1";
      };
      systemd.services.redis-frappe.serviceConfig = logFields { role = "redis"; };
    })

    # Per-site nginx virtualHosts.
    (mkIf (lib.any (s: s.nginx.enable) (builtins.attrValues enabledSites)) {
      # nginx needs group membership to traverse the 0750 site directories.
      users.users.nginx.extraGroups = [ cfg.group ];

      # Only the node realtime server needed this. It validated sessions with an
      # HTTP request to the site's own FQDN, so that name had to resolve to local
      # nginx rather than to the public reverse proxy in front of this host. The
      # Python runtime calls the WSGI app in-process and makes no such request.
      # optionalAttrs rather than mkIf on the key: mkIf false would leave
      # "127.0.0.1" declared with no definition of ours, which only works because
      # NixOS itself always defines that key. Selecting the whole attrset does not
      # depend on that.
      networking.hosts = lib.optionalAttrs (!cfg.runtime.enable) {
        "127.0.0.1" = mapAttrsToList (name: _: name) (filterAttrs (_: s: s.nginx.enable) enabledSites);
      };

      # One nginx serves every site, so no APP_SITE on the unit; each access-log
      # entry carries the site in its JSON instead.
      systemd.services.nginx.serviceConfig = logFields { role = "nginx"; };

      services.nginx = {
        enable = true;
        recommendedProxySettings = true;
        recommendedGzipSettings = true;

        # nginx's stderr carries no severity, so its error log would reach the
        # journal as info, [emerg] and all. Over syslog, nginx maps its own levels
        # onto syslog's. mkDefault: the operator may want it elsewhere.
        logError = lib.mkDefault base.nixos.nginx.errorLog;

        # The access log to the journal as one JSON object per request, under
        # SYSLOG_IDENTIFIER=nginx_access (python-devops-nix's lib.nixos.nginx,
        # the format odoo-nix sends too), or off. At the http level, before the
        # vhosts, so every server{} inherits it; either way nginx no longer
        # writes its compiled-in /var/log/nginx/access.log.
        commonHttpConfig = base.nixos.nginx.accessLog cfg.logging.accessLog;

        # One upstream per site process that listens on a unix socket.
        upstreams =
          lib.mapAttrs' (
            name: siteCfg:
            nameValuePair (upstreamName name) {
              servers."unix:${siteCfg.web.socketPath}" = { };
            }
          ) (filterAttrs (_: s: s.nginx.enable && s.web.socketPath != "") enabledSites)
          // lib.optionalAttrs (!cfg.runtime.enable) (
            lib.mapAttrs' (
              name: siteCfg:
              nameValuePair (socketioUpstreamName name) {
                servers."unix:${siteCfg.socketio.socketPath}" = { };
              }
            ) (filterAttrs (_: s: s.nginx.enable && s.socketio.socketPath != "") enabledSites)
          );

        virtualHosts = mapAttrs (name: siteCfg: mkSiteNginxVhost name siteCfg) (
          filterAttrs (_: s: s.nginx.enable) enabledSites
        );
      };
    })
  ]);
}
