# N1's checks (docs/app-standards/spec.md §7, "N1: runtime"), the parts that need no
# running bench. selftest-runtime.yml runs the rest against the fixture app.
#
#   standards-ports           the port offset: the primary checkout's is today's
#                            (a hash of name); a linked worktree's differs
#                            only with ports.worktreeSalt, which defaults to the
#                            opt-in test (on for an app with [tool.frappe-nix],
#                            off without); FRAPPE_NIX_PORT_OFFSET parses and
#                            wins; one offset sets web, db and the three
#                            Mailpit ports, through the real module. Those
#                            are preferred ports: frappe-ports leases them at
#                            `devenv up` (lib/port-lease.py, tests/port-lease.nix),
#                            first, with nginx, Mailpit and frappe:config
#                            after it, strict in bench mode.
#   standards-ci-mode         FRAPPE_NIX_CI's enterShell snippets, rendered and
#                            run: in CI mode no node verify, no node_modules, a
#                            one-line banner and the two exports; outside it the
#                            interactive path; and modules/devenv.nix wires them.
#   standards-bench-dev-group the bench root's dev group: the template keeps
#                            ruff, pre-commit and semgrep and adds coverage and
#                            unittest-xml-reporting; an opted-in app whose
#                            tools/pyproject.toml pins a replacement loses that
#                            one from its generated root (also when ensure-root
#                            reconciles a root that had it); every other root,
#                            an app-mode root that has not opted in and a
#                            bench-mode root, keeps all three, byte for byte.
#                            A root that has not opted in keeps coverage and
#                            unittest-xml-reporting only where its committed
#                            lock's own root lists them (no lock: neither), so
#                            a lock main produced, version-15 or version-16,
#                            gets main's root.
#   standards-relock-main-lock a version-16 app that has not opted in, with the
#                            nix/uv.lock frappe-nix main's relock wrote for it:
#                            its generated root is main's, `uv lock --check`
#                            passes, and a relock leaves the lock byte for
#                            byte (S35). The template's root, with both test
#                            tools, fails the same check.
#   standards-coverage-env    `import coverage` (and xmlrunner) works in the dev
#                            virtualenv lib/python.nix builds from that group.
#   standards-frappe-test     frappe-test builds, parses its
#                            arguments, and runs the stages its plan turns on
#                            against a dev shell of stubs.
{
  pkgs,
  lib,
  self,
  inputs,
  ...
}:

let
  # python-devops-nix's lib.ports and lib.ciMode, as modules/devenv.nix has
  # them (lib/shell-tools.nix).
  ports = inputs.python-devops-nix.lib.ports;
  inherit
    (import ../../lib/shell-tools.nix {
      inherit pkgs;
      base = inputs.python-devops-nix.lib;
    })
    ciMode
    ;
  parseEnvOffset = ports.parseEnvOffset {
    label = "frappe-nix";
    variable = "FRAPPE_NIX_PORT_OFFSET";
  };

  # --- ports, through the module ----------------------------------------------

  benchFlake = import ./fixtures/bench-flake.nix { inherit self pkgs; };

  portsOf =
    extra:
    let
      config = benchFlake (_: {
        frappe-nix = {
          enable = true;
          name = "example-bench";
          workspaceRoot = ../fixtures/lock-audit;
        }
        // extra;
      });
      shell = config.devenv.shells.default;
      mail = config.frappe-nix.mailpit;
    in
    {
      inherit (config.frappe-nix.ports) offset;
      web = shell.processes.nginx.ports.main.allocate;
      db = shell.processes.mysql.ports.main.allocate;
      inherit (config.frappe-nix.ports) socketio;
      smtp = mail.port;
      http = mail.httpPort;
      pop3 = mail.pop3.port;
    };

  today = portsOf { };
  # A bench-mode shell, as `devenv up` runs it.
  shellOf =
    extra:
    (benchFlake (_: {
      frappe-nix = {
        enable = true;
        name = "example-bench";
        workspaceRoot = ../fixtures/lock-audit;
      }
      // extra;
    })).devenv.shells.default;
  benchShell = shellOf { };
  # What `devenv up` runs before process-compose, in bench mode.
  benchUpPreamble = benchShell.process.manager.before;
  # The same with ports.strict = false, ports.base set, and sockets off.
  looseShell = shellOf { ports.strict = false; };
  baseShell = shellOf { ports.base = 8111; };
  tcpShell = shellOf {
    sockets.enable = false;
    ports.strict = false;
  };
  set123 = portsOf { ports.offset = 123; };

  # ports.worktreeSalt's default, read off the module for an app-mode config
  # whose source opted in or not. Nothing else of app mode is evaluated.
  saltDefault =
    src:
    (benchFlake (_: {
      frappe-nix = {
        enable = true;
        app = {
          enable = true;
          inherit src;
        };
      };
    })).frappe-nix.ports.worktreeSalt;
  optedInApp = ../fixtures/standards-app;
  plainApp = ./fixtures/optin/without-table;

  seed =
    args:
    ports.seed (
      {
        name = "standards-fixture";
        salt = true;
        pwd = "/src/standards-fixture";
        gitKind = "directory";
      }
      // args
    );
  worktreeA = ports.offsetFor (seed {
    pwd = "/src/wt-a";
    gitKind = "regular";
  });
  worktreeB = ports.offsetFor (seed {
    pwd = "/src/wt-b";
    gitKind = "regular";
  });

  portFacts =
    assert lib.assertMsg
      (
        contains "webBase = cfg.ports.http;" devenvSource
        && contains "dbBase = cfg.ports.db;" devenvSource
        && contains "tree = ports.options (" devenvSource
        && contains "bases = portBases;" devenvSource
      )
      "devenv.nix: the web or db port no longer comes from lib.ports.options, through ports.http and ports.db";
    assert lib.assertMsg (
      contains "default = config.frappe-nix.ports.smtp;" devenvSource
      && contains "default = config.frappe-nix.ports.mailUi;" devenvSource
      && contains "default = config.frappe-nix.ports.pop3;" devenvSource
      && contains (lib.concatStringsSep "\n" [
        "    if fcfg.app.enable then"
        "      ports.effectiveOffset envPortOffset fcfg.ports.offset"
        "    else"
        "      fcfg.ports.offset;"
      ]) devenvSource
      && contains "portOffset = portOffsetOf cfg;" devenvSource
    ) "devenv.nix: the Mailpit defaults or the bases no longer derive from the one offset";
    assert lib.assertMsg
      (
        contains "saltApplies = config.frappe-nix.app.enable;" devenvSource
        && contains "envApplies = config.frappe-nix.app.enable;" devenvSource
      )
      "devenv.nix: ports.offset's seed is not salted by app mode and ports.worktreeSalt, or FRAPPE_NIX_PORT_OFFSET applies outside app mode";
    # The ports are leased at `devenv up` (spec §5.12): frappe-ports first,
    # everything that binds or reads a leased port after it.
    assert lib.assertMsg (
      benchShell.processes ? frappe-ports
      && lib.hasInfix "frappe-nix-port-lease hold" benchShell.processes.frappe-ports.exec
      && lib.hasInfix "frappe-nix-port-lease ready" benchShell.processes.frappe-ports.ready.exec
      && benchShell.processes.frappe-ports.after == [ ]
      && benchShell.processes.frappe-ports.restart.on == "never"
    ) "no frappe-ports process holding the leases";
    assert lib.assertMsg (
      lib.all (p: lib.elem "devenv:processes:frappe-ports" benchShell.processes.${p}.after) [
        "nginx"
        "mailpit"
      ]
      && lib.elem "devenv:processes:frappe-ports" benchShell.tasks."frappe:config".after
    ) "nginx, Mailpit or frappe:config does not start after frappe-ports";
    assert lib.assertMsg (
      lib.hasInfix "frappe-nix/nginx.conf" benchShell.processes.nginx.exec
      && lib.hasInfix "/__frappe_nix/bench" benchShell.processes.nginx.ready.exec
      && lib.hasInfix "ports.json" benchShell.processes.mailpit.exec
      && lib.hasInfix "ports.json" benchShell.processes.mailpit.ready.exec
      && lib.hasInfix "jq -er .web" benchShell.tasks."frappe:config".exec
      && benchShell.env.FRAPPE_NIX_PORTS_FILE == "${benchShell.env.DEVENV_RUNTIME}/frappe-nix/ports.json"
    ) "nginx, Mailpit, frappe:config or FRAPPE_NIX_PORTS_FILE do not read the leased ports";
    assert lib.assertMsg (
      lib.hasInfix "listen 127.0.0.1:@WEB_PORT@;" benchShell.services.nginx.httpConfig
      && lib.hasInfix ''return 200 "@BENCH_ID@";'' benchShell.services.nginx.httpConfig
      && lib.hasInfix ''add_header X-Frappe-Nix-Bench "@BENCH_ID@" always;'' benchShell.services.nginx.httpConfig
    ) "nginx's config is not a template answering the bench's identity";
    # Bench mode is strict (its web port is committed), before anything starts
    # and in the holder; the old bound-port check is gone.
    assert lib.assertMsg (
      lib.hasInfix "_frappe_nix_strict=--strict
" benchUpPreamble
      && lib.hasInfix "hold --dry-run $_frappe_nix_strict" benchUpPreamble
      && lib.hasInfix "_frappe_nix_strict=--strict
" benchShell.processes.frappe-ports.exec
      && !(contains "portsFreeCheck" devenvSource)
    ) "bench mode's devenv up is not strict, or the old bound-port check is still there";
    # FRAPPE_NIX_STRICT_PORTS=0 never loosens bench mode; it does app mode's.
    assert lib.assertMsg (
      lib.hasInfix "0 | false | no) : ;;" benchUpPreamble
      && contains ''0 | false | no) ''${if appMode && sockets then "_frappe_nix_strict=" else ":"} ;;'' devenvSource
      && contains "!appMode;" devenvSource
    ) "FRAPPE_NIX_STRICT_PORTS=0 loosens bench mode, or app mode is not loose by default";
    assert lib.assertMsg (
      lib.hasInfix "_frappe_nix_strict=
" looseShell.process.manager.before
      && lib.hasInfix "--pin web" baseShell.process.manager.before
      && lib.hasInfix "--base 8111" baseShell.process.manager.before
      && lib.hasInfix "_frappe_nix_strict=--strict
" tcpShell.process.manager.before
      && lib.hasInfix "'web/web=127.0.0.1:" tcpShell.processes.frappe-ports.exec
    ) "ports.strict = false, ports.base's pin or TCP mode's strictness is not what the lease runs";
    # The values every bench had before this change.
    assert lib.assertMsg (
      ports.offsetFor "example-bench" == 740
    ) "offsetFor changed: example-bench is no longer 740";
    assert lib.assertMsg (
      today == {
        offset = 740;
        web = 8740;
        db = 4046;
        socketio = 9740;
        smtp = 19740;
        http = 20740;
        pop3 = 21740;
      }
    ) "a bench's default ports moved: ${builtins.toJSON today}";
    assert lib.assertMsg (
      set123 == {
        offset = 123;
        web = 8123;
        db = 3429;
        socketio = 9123;
        smtp = 19123;
        http = 20123;
        pop3 = 21123;
      }
    ) "ports.offset = 123 does not set every port: ${builtins.toJSON set123}";
    # [1.2] The salt follows the opt-in (S35, §5.12).
    assert lib.assertMsg (saltDefault optedInApp)
      "ports.worktreeSalt is off for an app with [tool.frappe-nix]";
    assert lib.assertMsg (
      !(saltDefault plainApp)
    ) "ports.worktreeSalt is on for an app without [tool.frappe-nix]";
    # The primary checkout (.git is a directory) keeps the name hash.
    assert lib.assertMsg (seed { } == "standards-fixture") "the primary checkout's seed is salted";
    assert lib.assertMsg (
      seed {
        pwd = "";
        gitKind = null;
      } == "standards-fixture"
    ) "pure evaluation salts the seed";
    assert lib.assertMsg
      (
        seed {
          salt = false;
          gitKind = "regular";
        } == "standards-fixture"
      )
      "without the salt (bench mode, or an app that has not opted in) a linked worktree's seed is salted";
    assert lib.assertMsg (
      seed { gitKind = "regular"; } == "standards-fixture@/src/standards-fixture"
    ) "with the salt, a linked worktree's seed is not salted with its path";
    assert lib.assertMsg (
      worktreeA != worktreeB && worktreeA != ports.offsetFor "standards-fixture"
    ) "two worktrees of one app share an offset (${toString worktreeA}, ${toString worktreeB})";
    assert lib.assertMsg (parseEnvOffset "123" == 123) "FRAPPE_NIX_PORT_OFFSET=123 is not 123";
    assert lib.assertMsg (parseEnvOffset "007" == 7) "FRAPPE_NIX_PORT_OFFSET=007 is not 7";
    assert lib.assertMsg (parseEnvOffset "0" == 0) "FRAPPE_NIX_PORT_OFFSET=0 is not 0";
    assert lib.assertMsg (parseEnvOffset "" == null) "an unset FRAPPE_NIX_PORT_OFFSET is not null";
    assert lib.assertMsg (ports.effectiveOffset 123 740 == 123) "FRAPPE_NIX_PORT_OFFSET does not win";
    assert lib.assertMsg (
      ports.effectiveOffset null 740 == 740
    ) "the option is ignored without the variable";
    {
      inherit today set123;
      worktrees = [
        worktreeA
        worktreeB
      ];
      primary = ports.offsetFor "standards-fixture";
      worktreeSalt = {
        optedIn = saltDefault optedInApp;
        notOptedIn = saltDefault plainApp;
      };
    };

  # --- CI mode ----------------------------------------------------------------

  devenvSource = builtins.readFile ../../modules/devenv.nix;
  # Not lib.hasInfix: its `.*infix.*` regex over a 150 KB file overflows the
  # regex engine's stack on some Nix builds (2.35 on CI's runners).
  contains = needle: haystack: builtins.replaceStrings [ needle ] [ "" ] haystack != haystack;
  ciShell = pkgs.writeText "ci-mode-enter-shell" ''
    ${ciMode.exports}
    ${ciMode.unlessCi ''
      frappe-nix-node-verify "$FRAPPE_BENCH_ROOT" frappe
      frappe-nix-node-modules "$FRAPPE_BENCH_ROOT" frappe
    ''}
    ${ciMode.banner {
      name = "standards-fixture";
      interactive = "echo interactive-banner";
    }}
    echo "DEVENV_IN_DIRENV_SHELL=''${DEVENV_IN_DIRENV_SHELL:-unset} PC_TUI_ENABLED=''${PC_TUI_ENABLED:-unset}"
  '';

  # --- the bench root's dev group ---------------------------------------------

  template = builtins.fromTOML (builtins.readFile ../../templates/bench/pyproject.toml);
  templateDev = template.dependency-groups.dev;
  reqName = r: builtins.head (builtins.match "([A-Za-z0-9_.-]+).*" r);
  appPinned = [
    "ruff"
    "pre-commit"
    "semgrep"
  ];
  # The coverage-env fixture is the dev group of an opted-in app that pins all
  # three itself: the template's, without them.
  devGroupFixture = ./fixtures/bench-dev-group;
  fixtureDev =
    (builtins.fromTOML (builtins.readFile (devGroupFixture + "/pyproject.toml"))).dependency-groups.dev;

  # The app-mode root lib/app-workspace.nix generates, for the fixture app,
  # without opting in (appTools = null) and with a tools/pyproject.toml that
  # pins all three tools or only ruff. Source files, not derivations:
  # lib/app-workspace.nix tests pathExists on them at evaluation, which
  # `nix flake check --no-build` cannot build for.
  toolsAll = ./fixtures/app-tools/all/pyproject.toml;
  toolsRuff = ./fixtures/app-tools/ruff/pyproject.toml;
  # A non-opted-in app's committed lock: a version-15 one (no coverage, no
  # unittest-xml-reporting anywhere); a version-16 one main's relock wrote
  # (both as packages, through a `test` extra, but not in its root's dev
  # group); and one whose root lists both in its dev group, as a relock under
  # this template writes it (the dev-group fixture's). And none at all: a path
  # that does not exist, as lib/app-workspace.nix sees an app with no lock yet.
  v15Lock = ./fixtures/v15-lock/uv.lock;
  mainV16Lock = ./fixtures/main-v16-lock/uv.lock;
  rootListsLock = devGroupFixture + "/uv.lock";
  noLock = ./fixtures/no-such-dir/uv.lock;
  appRoot = appTools: appRootWith { inherit appTools; };
  appRootWith =
    {
      appTools ? null,
      testToolsLock ? null,
    }:
    appWorkspaceWith { inherit appTools testToolsLock; } + "/pyproject.toml";
  appWorkspaceWith =
    {
      appTools ? null,
      testToolsLock ? null,
      lockFile ? null,
      app ? {
        name = "standards_fixture";
        src = ../fixtures/standards-app;
      },
    }:
    import ../../lib/app-workspace.nix {
      inherit
        pkgs
        lib
        appTools
        testToolsLock
        lockFile
        ;
      apps = [ app ];
      projectName = "${lib.replaceStrings [ "_" ] [ "-" ] app.name}-bench";
      preset = (lib.importJSON ../../lib/frappe-presets.json).version-16;
    };
  # The version-16 app main-v16-lock was locked for, and its workspace here:
  # with that lock, the way modules/devenv.nix assembles an app that has not
  # opted in (testToolsLock is the lock), and the template's root (what the
  # lock-name test before the S35 fix kept for every version-16 lock).
  lockCheckApp = {
    name = "lock_check_app";
    src = ./fixtures/lock-check-app;
  };
  mainLockWorkspace = appWorkspaceWith {
    app = lockCheckApp;
    lockFile = mainV16Lock;
    testToolsLock = mainV16Lock;
  };
  templateLockWorkspace = appWorkspaceWith {
    app = lockCheckApp;
    lockFile = mainV16Lock;
  };
  envs = import ../../lib/python.nix {
    inherit pkgs lib;
    python = pkgs.python314;
    workspaceRoot = devGroupFixture;
    name = "standards-dev-group";
    inherit (inputs)
      pyproject-nix
      pyproject-build-systems
      uv2nix
      python-devops-nix
      ;
    lockAuditRelock = ''
      tests/standards/fixtures/bench-dev-group no longer matches its uv.lock:

          uv lock --project tests/standards/fixtures/bench-dev-group
    '';
  };
  workspaceTool = import ../../lib/workspace-tool.nix { inherit pkgs; };

  frappeTest =
    (import ../../lib/scripts.d/frappe-test.nix {
      inherit lib pkgs;
      appMode = true;
    }).frappe-test.exec;
in
{
  standards-ports = pkgs.writeText "standards-ports.json" (builtins.toJSON portFacts);

  standards-ci-mode =
    assert lib.assertMsg (contains "\${ciMode.exports}" devenvSource)
      "devenv.nix: enterShell lost ciMode.exports";
    assert lib.assertMsg (
      # One contiguous block: both node steps are the body of ciMode.unlessCi, so
      # moving either out of it breaks the match.
      contains (lib.concatStringsSep "\n" [
        "ciMode.unlessCi ''"
        "                  \${nodeVerifyTool}/bin/frappe-nix-node-verify \"$FRAPPE_BENCH_ROOT\" \${lib.escapeShellArgs benchInfra.appsWithNode} || true"
        "                  \${nodeModulesTool}/bin/frappe-nix-node-modules \"$FRAPPE_BENCH_ROOT\" \${lib.escapeShellArgs benchInfra.appsWithNode} || true"
        "                ''"
      ]) devenvSource
    ) "devenv.nix: the node steps are not wrapped in ciMode.unlessCi";
    assert lib.assertMsg (contains "\${ciMode.banner {" devenvSource)
      "devenv.nix: the banner is not ciMode.banner";
    pkgs.runCommand "standards-ci-mode-check" { } ''
      set -euo pipefail
      fail() { echo "FAIL $*" >&2; exit 1; }
      mkdir bin
      for tool in frappe-nix-node-verify frappe-nix-node-modules; do
        printf '#!%s\necho "%s ran"\n' ${lib.getExe pkgs.bash} "$tool" > "bin/$tool"
        chmod +x "bin/$tool"
      done
      export PATH="$PWD/bin:$PATH" FRAPPE_BENCH_ROOT=/bench FRAPPE_SITE=fixture.localhost

      ci="$(env FRAPPE_NIX_CI=1 ${lib.getExe pkgs.bash} ${ciShell})"
      echo "$ci"
      ! grep -q 'ran$' <<< "$ci" || fail "CI mode ran a node step"
      grep -qx 'frappe-nix: CI mode (standards-fixture, site fixture.localhost)' <<< "$ci" || fail "CI mode banner"
      ! grep -q interactive-banner <<< "$ci" || fail "CI mode printed the interactive banner"
      grep -q 'DEVENV_IN_DIRENV_SHELL=true PC_TUI_ENABLED=0' <<< "$ci" || fail "CI mode exports"
      echo "ok   FRAPPE_NIX_CI=1: no node steps, one-line banner, exports"

      for off in "" 0 false; do
        shell="$(env FRAPPE_NIX_CI="$off" ${lib.getExe pkgs.bash} ${ciShell})"
        grep -qx 'frappe-nix-node-verify ran' <<< "$shell" || fail "FRAPPE_NIX_CI='$off' skipped node verify"
        grep -qx 'frappe-nix-node-modules ran' <<< "$shell" || fail "FRAPPE_NIX_CI='$off' skipped node_modules"
        grep -qx interactive-banner <<< "$shell" || fail "FRAPPE_NIX_CI='$off' lost the banner"
        grep -q 'DEVENV_IN_DIRENV_SHELL=unset' <<< "$shell" || fail "FRAPPE_NIX_CI='$off' exported CI variables"
      done
      echo "ok   unset, 0 and false: the interactive shell"

      # An empty body is still valid shell.
      ${lib.getExe pkgs.bash} -n ${pkgs.writeText "empty" (ciMode.unlessCi "")}
      cp ${ciShell} "$out"
    '';

  standards-bench-dev-group =
    assert lib.assertMsg
      (fixtureDev == builtins.filter (r: !(builtins.elem (reqName r) appPinned)) templateDev)
      "tests/standards/fixtures/bench-dev-group's dev group is not the template's without ruff, pre-commit and semgrep: re-copy it and re-lock";
    assert lib.assertMsg
      (contains "appTools = if standardsShell.optedIn then cfg.app.src + \"/tools/pyproject.toml\" else null;" devenvSource)
      "devenv.nix: the generated root's appTools is not gated on the opt-in";
    assert lib.assertMsg
      (contains "testToolsLock = if standardsShell.optedIn then null else lockPath;" devenvSource)
      "devenv.nix: the generated root of an app that has not opted in does not keep to its lock's test tools";
    pkgs.runCommand "standards-bench-dev-group-check"
      {
        nativeBuildInputs = [
          workspaceTool
          pkgs.python3
        ];
      }
      ''
        set -euo pipefail
        fail() { echo "FAIL $*" >&2; exit 1; }
        names() { python3 -c 'import re, sys, tomllib; print(" ".join(sorted(re.split(r"[<>=!~ ;\[]", r)[0] for r in tomllib.load(open(sys.argv[1], "rb"))["dependency-groups"]["dev"])))' "$1"; }
        has() { case " $(names "$1") " in *" $2 "*) return 0 ;; *) return 1 ;; esac; }
        expect() { # <label> <file> <present…> -- <absent…>
          local label="$1" file="$2" want=1
          shift 2
          for name in "$@"; do
            if [ "$name" = -- ]; then want=0; continue; fi
            if [ "$want" = 1 ]; then
              has "$file" "$name" || fail "$label: no $name in [$(names "$file")]"
            else
              ! has "$file" "$name" || fail "$label: $name in [$(names "$file")]"
            fi
          done
          echo "ok   $label: $(names "$file")"
        }

        expect "the template keeps all three and adds coverage and xmlrunner" ${../../templates/bench/pyproject.toml} \
          coverage unittest-xml-reporting ruff pre-commit semgrep
        expect "an app-mode root nobody passed a lock for (tests/app-workspace.nix)" ${appRoot null} \
          coverage unittest-xml-reporting ruff pre-commit semgrep
        expect "an app-mode root that has not opted in, with no lock yet" ${
          appRootWith { testToolsLock = noLock; }
        } \
          ruff pre-commit semgrep pydantic pytest responses -- coverage unittest-xml-reporting
        v16=${appRootWith { testToolsLock = mainV16Lock; }}
        expect "an app-mode root that has not opted in, with the version-16 lock main wrote" "$v16" \
          ruff pre-commit semgrep pydantic pytest responses -- coverage unittest-xml-reporting
        expect "an app-mode root that has not opted in, with a lock whose root lists both" ${
          appRootWith { testToolsLock = rootListsLock; }
        } \
          coverage unittest-xml-reporting ruff pre-commit semgrep
        v15=${appRootWith { testToolsLock = v15Lock; }}
        expect "an app-mode root that has not opted in, with a version-15 lock" "$v15" \
          ruff pre-commit semgrep pydantic pytest responses -- coverage unittest-xml-reporting
        # What the lock audit (lib/python.nix) asks of the root's dev group: every name in the lock.
        python3 - "$v15" ${v15Lock} <<'PY'
        import re, sys, tomllib
        dev = tomllib.load(open(sys.argv[1], "rb"))["dependency-groups"]["dev"]
        locked = {p["name"] for p in tomllib.load(open(sys.argv[2], "rb"))["package"]}
        missing = [r for r in dev if re.split(r"[<>=!~ ;\[]", r)[0] not in locked]
        if missing:
            sys.exit(f"FAIL the version-15 root needs a relock for {missing}")
        PY
        # The root the old template rendered, byte for byte: the same lock resolves it.
        diff "$v15" <(sed -e '/"coverage>=/d' -e '/"unittest-xml-reporting>=/d' ${appRoot null}) \
          || fail "the version-15 root differs from the template without the two test tools"
        echo "ok   a version-15 lock covers that root's dev group: no relock after upgrading frappe-nix"
        cmp -s "$v15" "$v16" || fail "the version-16 root differs from the version-15 one"
        cmp -s "$v15" ${
          appRootWith { testToolsLock = noLock; }
        } || fail "the no-lock root differs from the version-15 one"
        echo "ok   no lock, a version-15 lock and main's version-16 lock: the same root, main's"
        expect "an opted-in root whose tools/pyproject.toml lists ruff, semgrep and prek" ${appRoot toolsAll} \
          coverage unittest-xml-reporting pydantic -- ruff pre-commit semgrep
        expect "an opted-in root whose tools/pyproject.toml lists only ruff" ${appRoot toolsRuff} \
          coverage unittest-xml-reporting pre-commit semgrep -- ruff

        # ensure-root over a root that already has them: an opted-in app's drops
        # them; every other root, app mode or bench mode, is left byte for byte.
        cp ${appRoot null} plain.toml && chmod u+w plain.toml
        cp plain.toml before.toml
        frappe-nix-workspace ensure-root --pyproject plain.toml
        cmp -s plain.toml before.toml || fail "ensure-root changed an app-mode root that has not opted in"
        frappe-nix-workspace ensure-root --pyproject plain.toml --template ${../../templates/bench/pyproject.toml}
        cmp -s plain.toml before.toml || fail "ensure-root (root sync, bench mode) changed a root with all three"
        echo "ok   ensure-root leaves a root that has not opted in, and a bench-mode root, byte for byte"

        cp before.toml opted.toml
        frappe-nix-workspace ensure-root --pyproject opted.toml --app-tools ${toolsAll} > opted.log
        expect "ensure-root --app-tools (ruff, semgrep, prek) on a root that had them" opted.toml \
          coverage unittest-xml-reporting pydantic pytest responses -- ruff pre-commit semgrep
        grep -q 'dev -= ruff>=0.15.0' opted.log || fail "ensure-root does not say what it dropped: $(cat opted.log)"
        cp opted.toml again.toml
        frappe-nix-workspace ensure-root --pyproject again.toml --app-tools ${toolsAll}
        cmp -s opted.toml again.toml || fail "a second ensure-root --app-tools changed the file"
        cp before.toml ruff.toml
        frappe-nix-workspace ensure-root --pyproject ruff.toml --app-tools ${toolsRuff}
        expect "ensure-root --app-tools (ruff only)" ruff.toml pre-commit semgrep -- ruff
        cp before.toml missing.toml
        frappe-nix-workspace ensure-root --pyproject missing.toml --app-tools "$PWD/no-such-file.toml"
        cmp -s missing.toml before.toml || fail "an absent tools/pyproject.toml dropped something"
        echo "ok   no tools/pyproject.toml, nothing dropped"

        # --test-tools-lock reads the lock's root, never a package elsewhere in it.
        cp before.toml locked.toml
        frappe-nix-workspace ensure-root --pyproject locked.toml --test-tools-lock ${mainV16Lock} > locked.log
        expect "ensure-root --test-tools-lock (main's version-16 lock)" locked.toml \
          ruff pre-commit semgrep -- coverage unittest-xml-reporting
        grep -q 'dev -= coverage>=7.10' locked.log || fail "ensure-root does not say what it dropped: $(cat locked.log)"
        cp before.toml listed.toml
        frappe-nix-workspace ensure-root --pyproject listed.toml --test-tools-lock ${rootListsLock}
        cmp -s listed.toml before.toml || fail "a lock whose root lists both dropped something"
        cp before.toml unlocked.toml
        frappe-nix-workspace ensure-root --pyproject unlocked.toml --test-tools-unlocked
        expect "ensure-root --test-tools-unlocked" unlocked.toml ruff pre-commit semgrep -- coverage unittest-xml-reporting
        cp before.toml gone.toml
        frappe-nix-workspace ensure-root --pyproject gone.toml --test-tools-lock "$PWD/no-such-lock"
        cmp -s gone.toml unlocked.toml || fail "a missing lock file is not the same as no lock"
        touch "$out"
      '';

  standards-relock-main-lock =
    pkgs.runCommand "standards-relock-main-lock-check"
      {
        nativeBuildInputs = [
          pkgs.uv
          pkgs.python314
        ];
      }
      ''
        set -euo pipefail
        fail() { echo "FAIL $*" >&2; exit 1; }
        export HOME="$TMPDIR" UV_CACHE_DIR="$TMPDIR/uv-cache" UV_NO_CONFIG=1 UV_OFFLINE=1 \
          UV_PYTHON=${lib.getExe pkgs.python314} UV_PYTHON_DOWNLOADS=never UV_PYTHON_PREFERENCE=only-system
        unset SSL_CERT_FILE NIX_SSL_CERT_FILE

        # main's root: the template's, byte for byte, without the two test tools.
        diff ${mainLockWorkspace}/pyproject.toml \
          <(sed -e '/"coverage>=/d' -e '/"unittest-xml-reporting>=/d' ${templateLockWorkspace}/pyproject.toml) \
          || fail "the root of an app that has not opted in, with main's version-16 lock, is not main's"
        echo "ok   the generated root is main's, byte for byte"

        # `nix run .#relock`'s steps (modules/devenv.nix), offline: a writable
        # copy of the workspace, the committed lock seeded, `uv lock`.
        relock() { # <workspace> <dir>
          cp -a "$1/." "$2/" && chmod -R u+w "$2"
          install -m 0644 ${mainV16Lock} "$2/uv.lock"
        }
        mkdir main template
        relock ${mainLockWorkspace} main
        (cd main && uv lock --check -v) > main.log 2>&1 || { cat main.log; fail "main's version-16 lock is stale under this root"; }
        ! grep -q 'Resolving despite existing lockfile' main.log || { cat main.log; fail "uv re-resolved main's lock"; }
        (cd main && uv lock)
        cmp -s main/uv.lock ${mainV16Lock} || { diff -u ${mainV16Lock} main/uv.lock || true; fail "a relock rewrote main's lock"; }
        echo "ok   main's version-16 lock is current, and a relock leaves it byte for byte"

        # The control: the template's root, both test tools in it, is what the
        # lock-name test produced. The same check has to catch it.
        # Offline, uv cannot finish that relock (it would fetch frappe-runtime's
        # git source), so this reads why it started one: the root's dev group.
        relock ${templateLockWorkspace} template
        if (cd template && uv lock --check -v) > template.log 2>&1; then
          fail "uv lock --check passed on the template's root: this check cannot see the regression"
        fi
        grep -q 'Resolving despite existing lockfile due to mismatched dependency groups' template.log \
          || { cat template.log; fail "uv lock --check failed on the template's root for another reason"; }
        echo "ok   the template's root (coverage and unittest-xml-reporting) would make that lock stale"
        touch "$out"
      '';

  standards-coverage-env = pkgs.runCommand "standards-coverage-env-check" { } ''
    set -euo pipefail
    ${envs.devPythonEnv}/bin/python -c 'import coverage, xmlrunner; print("coverage", coverage.__version__)'
    site="$(${envs.devPythonEnv}/bin/python -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
    for bad in ruff pre_commit semgrep; do
      if [ -e "$site/$bad" ]; then
        echo "FAIL $bad is in the dev env" >&2
        exit 1
      fi
    done
    touch "$out"
  '';

  standards-frappe-test =
    pkgs.runCommand "standards-frappe-test-check"
      {
        nativeBuildInputs = [
          pkgs.git
          pkgs.python3
          pkgs.curl
          pkgs.coreutils
          pkgs.jq
          pkgs.util-linux
        ];
      }
      ''
        set -euo pipefail
        fail() { echo "FAIL $*" >&2; exit 1; }
        cat > frappe-test <<'EOF'
        #!${lib.getExe pkgs.bash}
        ${frappeTest}
        EOF
        chmod +x frappe-test
        ./frappe-test --help | grep -q -- '--reuse-site'
        rc=0; ./frappe-test --no-such-flag 2> err || rc=$?
        [ "$rc" = 64 ] || fail "an unknown flag is exit $rc, not 64 (usage)"
        rc=0; env -u FRAPPE_BENCH_ROOT ./frappe-test 2> err || rc=$?
        [ "$rc" = 10 ] && grep -q 'app dev shell' err || fail "outside a shell: rc $rc, $(cat err)"
        echo "ok   usage errors exit 64, no dev shell exits 10"

        # A dev shell of stubs: the bench answers, every call is logged. frappe-nix
        # is the real one frappe-test carries, so the plan is read from the
        # repository's real pyproject.toml.
        export HOME="$PWD" STATE="$PWD/state" GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t
        mkdir -p "$STATE" stubs bench/env/bin bench/sites/dev.localhost bench/apps
        port=18765
        python3 -m http.server --bind 127.0.0.1 "$port" > /dev/null 2>&1 &
        server=$!
        trap 'kill "$server" 2> /dev/null || true' EXIT
        for _ in $(seq 50); do curl -s -o /dev/null "http://127.0.0.1:$port/" && break; sleep 0.2; done
        echo "{\"webserver_port\": $port}" > bench/sites/common_site_config.json
        stub() { # <path> <body>
          printf '#!%s\necho "%s $*" >> "$STATE/log"\n%s\n' ${lib.getExe pkgs.bash} "$(basename "$1")" "$2" > "$1"
          chmod +x "$1"
        }
        # devenv fails as a taken port does, unless STUB_DEVENV_OK, when it brings the bench
        # up ($STATE/pc-up, which `process list` then answers for and `down` removes).
        stub stubs/devenv '[ -n "''${STUB_DEVENV_OK:-}" ] && touch "$STATE/pc-up" && exit 0
        echo "frappe-nix: cannot start fixture: already in use: nginx=127.0.0.1:8000" >&2
        echo "    FRAPPE_NIX_PORT_OFFSET=<n> nix develop --no-pure-eval   (or direnv reload)" >&2; exit 1'
        stub stubs/process-compose 'case "$*" in
          *" process list") [ -z "$STUB_PC_DOWN" ] || [ -e "$STATE/pc-up" ] ;;
          *" process get "*) grep -qx "''${@: -1}" "$STATE/procs" 2> /dev/null ;;
          *" down") rm -f "$STATE/pc-up" ;;
        esac'
        stub stubs/mariadb-admin 'exit 0'
        stub stubs/provision-site 'echo "provision-site created $FRAPPE_SITE" >> "$STATE/log"
        [ -z "''${STUB_PROVISION_FAILS:-}" ] || exit 1; mkdir -p "$FRAPPE_BENCH_ROOT/sites/$FRAPPE_SITE"'
        stub stubs/uv 'echo "error[x]: something"; echo "Found 3 diagnostics"; exit 1'
        stub bench/env/bin/bench 'exit 0'
        # The tests exit STUB_TESTS_RC; `coverage run` leaves its data file, `coverage
        # report` exits STUB_COV_REPORT_RC.
        stub bench/env/bin/python 'case "$*" in
          "-c import coverage") [ -z "''${STUB_NO_COVERAGE:-}" ] ;;
          "-c import xmlrunner") [ -z "''${STUB_NO_XMLRUNNER:-}" ] ;;
          *"-m coverage run "*)
            for a in "$@"; do case "$a" in --data-file=*) touch "''${a#--data-file=}" ;; esac; done
            exit "''${STUB_TESTS_RC:-0}" ;;
          *"-m coverage report"*) exit "''${STUB_COV_REPORT_RC:-0}" ;;
          *"-m coverage "*) exit 0 ;;
          *bench_helper*) exit "''${STUB_TESTS_RC:-0}" ;;
        esac'

        git init -q repo
        echo gen/ > repo/.gitignore
        mkdir repo/gen && echo v1 > repo/gen/types.ts
        printf '{ }\n' > repo/flake.nix
        # pyproject.toml as an opted-in app on the recommended profile: <extra>
        # is appended to [tool.frappe-nix], <tables> after it.
        pyproject() { # <extra> [<tables>]
          printf '[project]\nname = "fixture"\n\n[tool.frappe-nix]\nschema = 1\nfrappe-major = 16\nprofile = "recommended"\n%s\n\n[tool.frappe-nix.tests]\nsetup = ["execute:fixture.setup"]\n%s\n' \
            "$1" "''${2:-}" > repo/pyproject.toml
        }
        pyproject ""
        git -C repo add pyproject.toml .gitignore flake.nix && git -C repo add -f gen/types.ts && git -C repo commit -qm fixture
        ln -s "$PWD/repo" bench/apps/fixture

        export PATH="$PWD/stubs:$PATH" FRAPPE_BENCH_ROOT="$PWD/bench" DEVENV_ROOT="$PWD/repo" \
          FRAPPE_SITE=dev.localhost PC_SOCKET_PATH="$PWD/pc.sock" FRAPPE_DB_SOCKET=/nowhere \
          STUB_PC_DOWN=""
        run() { # <expected exit> <label> <args…>
          local want="$1" label="$2" rc=0
          shift 2
          : > "$STATE/log"
          timeout 120 ./frappe-test --out "$PWD/out" "$@" > run.log 2>&1 || rc=$?
          [ "$rc" = "$want" ] || { cat run.log "$STATE/log"; fail "$label: exit $rc, want $want"; }
        }
        # run, with a terminal on stdin and stderr that answers <answer> to the prompt.
        run_tty() { # <expected exit> <label> <answer> <args…>
          local want="$1" label="$2" answer="$3" rc=0
          shift 3
          : > "$STATE/log"
          printf '%s\n' "$answer" | timeout 120 script -qec "./frappe-test --out $PWD/out $(printf '%q ' "$@")" /dev/null > run.log 2>&1 || rc=$?
          [ "$rc" = "$want" ] || { cat run.log "$STATE/log"; fail "$label: exit $rc, want $want"; }
        }
        stage() { jq -r ".stages[\"$1\"]" out/frappe-test-report.json; }

        began="$SECONDS"
        STUB_PC_DOWN=1 run 10 "devenv up fails" --no-coverage --no-composition --reuse-site
        [ $((SECONDS - began)) -lt 60 ] || fail "a failed devenv up waited"
        grep -q 'FRAPPE_NIX_PORT_OFFSET' run.log && grep -q 'devenv up -D failed' run.log || fail "devenv up's message is lost: $(cat run.log)"
        [ "$(stage up)" = error ] || fail "no report with up=error: $(cat out/frappe-test-report.json)"
        ! grep -q 'mariadb-admin' "$STATE/log" || fail "it waited for the database after devenv up failed"
        echo "ok   a failed devenv up -D exits 10 at once, with devenv's message"

        run 0 "--site other.localhost" --no-coverage --no-composition --site other.localhost
        grep -qx 'provision-site created other.localhost' "$STATE/log" || fail "--site: provisioned $(grep created "$STATE/log")"
        grep -q 'bench --site other.localhost set-config allow_tests true' "$STATE/log" || fail "--site: allow_tests on the wrong site"
        grep -q 'bench --site other.localhost execute fixture.setup' "$STATE/log" || fail "--site: setup on the wrong site"
        echo "ok   --site S provisions S, not \$FRAPPE_SITE, and runs [tool.frappe-nix.tests] setup"

        # A version-15 app that has not opted in: its lock has no coverage.
        STUB_NO_COVERAGE=1 run 10 "no coverage in the bench's environment" --reuse-site --no-composition
        grep -q "environment has no coverage" run.log && grep -q -- '--no-coverage' run.log || fail "no message: $(cat run.log)"
        ! grep -q 'devenv' "$STATE/log" || fail "it started the bench without coverage: $(cat "$STATE/log")"
        STUB_NO_COVERAGE=1 run 0 "--no-coverage without coverage" --reuse-site --no-composition --no-coverage
        echo "ok   no coverage module: exit 10 before the bench starts, naming --no-coverage; --no-coverage runs"

        # The dev site exists: without --ci it is never dropped unasked.
        run 64 "an existing site, neither --reuse-site nor --recreate-site" --no-coverage --no-composition
        grep -q 'dev.localhost exists: pass --reuse-site' run.log || fail "no hint: $(cat run.log)"
        ! grep -q 'devenv\|provision-site\|bench ' "$STATE/log" || fail "it started something: $(cat "$STATE/log")"
        run 64 "--reuse-site with --recreate-site" --reuse-site --recreate-site
        run 0 "--recreate-site" --no-coverage --no-composition --recreate-site
        grep -qx 'provision-site created dev.localhost' "$STATE/log" || fail "--recreate-site did not recreate the site: $(cat "$STATE/log")"
        echo "ok   an existing site needs --reuse-site or --recreate-site outside --ci (exit 64, nothing started); --recreate-site recreates it"

        pyproject 'no-such-key = 1'
        run 10 "an invalid [tool.frappe-nix]" --no-coverage --reuse-site
        ! grep -q 'devenv\|run-tests' "$STATE/log" || fail "it started the bench with an unreadable configuration: $(cat "$STATE/log")"
        grep -q 'could not read the app.s configuration' run.log || fail "no message: $(cat run.log)"
        echo "ok   an invalid configuration exits 10 before anything starts"

        # --ci runs the stages the configuration turns on. tests off: no bench.
        pyproject "" $'enable = false\n\n[tool.frappe-nix.python-types]\nenable = false'
        run 0 "--ci with the tests and python-types modules off" --ci
        ! grep -q 'devenv\|process-compose\|provision-site\|bench ' "$STATE/log" || fail "tests off started the bench: $(cat "$STATE/log")"
        [ ! -e pc.sock ] || fail "a process-compose socket appeared"
        for name in up site tests coverage testmap composition ty; do
          [ "$(stage "$name")" = skipped ] || fail "tests off: stage $name is $(stage "$name"), not skipped"
        done
        [ "$(stage nix-lint)" = ok ] || fail "nix-lint (still on) is $(stage nix-lint)"
        echo "ok   --ci with tests off: stages 1 to 6 skipped and no bench; python-types off: stage 7 skipped; nix-lint ran"

        pyproject "" $'\n[tool.frappe-nix.nix-lint]\nenable = false\n\n[tool.frappe-nix.tests.coverage]\nenable = false'
        run 0 "--ci, coverage and nix-lint off" --ci --reuse-site --no-composition
        grep -q 'run-tests\|bench_helper' "$STATE/log" || grep -q 'tests: fixture' run.log || fail "the tests did not run: $(cat run.log)"
        [ "$(stage coverage)" = skipped ] && [ "$(stage nix-lint)" = skipped ] && [ "$(stage testmap)" = skipped ] \
          || fail "coverage off: coverage $(stage coverage), testmap $(stage testmap), nix-lint $(stage nix-lint)"
        echo "ok   --ci with coverage and nix-lint off: the tests run, coverage, testmap and nix-lint skipped"

        # An app without [tool.frappe-nix]: the recommended stages, nothing of its own.
        printf '[project]\nname = "fixture"\n' > repo/pyproject.toml
        run 0 "--ci without [tool.frappe-nix]" --ci --reuse-site --no-composition --no-coverage
        grep -q 'no \[tool.frappe-nix\] in pyproject.toml' run.log || fail "no notice: $(cat run.log)"
        grep -q 'bench --site dev.localhost execute frappe.utils.install.complete_setup_wizard' "$STATE/log" \
          || fail "not the default setup: $(cat "$STATE/log")"
        [ "$(stage nix-lint)" = ok ] && [ "$(stage ty)" = skipped ] || fail "recommended: nix-lint $(stage nix-lint), ty $(stage ty)"
        echo "ok   an app that has not opted in gets the recommended stages and the default setup"

        # The dev bench and the runtime directories exist and are ignored, as in a real app;
        # naming an ignored path in `git add` used to make every snapshot fail.
        mkdir -p repo/.frappe-nix/bench repo/.dev-dist
        printf '/.frappe-nix/\n/.dev-dist/\n' >> repo/.gitignore
        pyproject 'shell-checks = ["echo v1 > gen/types.ts; echo scratch > gen/other"]'
        run 0 "a clean shell check" --ci --reuse-site --no-composition --no-coverage
        [ "$(stage shell-checks)" = ok ] || fail "the clean shell check is $(stage shell-checks)"
        pyproject 'shell-checks = ["echo v2 > gen/types.ts"]'
        run 7 "a stale committed generator" --ci --reuse-site --no-composition --no-coverage
        grep -q 'gen/types.ts' out/shell-checks.log || fail "the changed file is not listed: $(cat out/shell-checks.log)"
        git -C repo checkout -q gen/types.ts
        echo "ok   --ci runs the shell checks; one that rewrites a tracked file under an ignored path fails (exit 7)"

        pyproject 'shell-checks = ["exit 3"]'
        run 7 "a failing shell check, without --ci" --shell-checks --reuse-site --no-composition --no-coverage
        grep -q 'exited 3' out/shell-checks.log || fail "the exit is not logged: $(cat out/shell-checks.log)"
        echo "ok   --shell-checks turns the shell checks on without --ci; a failing one is exit 7, its status logged"

        # Usage errors, and the app and the site to test.
        pyproject ""
        rc=0; ./frappe-test --app 2> err || rc=$?
        [ "$rc" = 64 ] && grep -q -- '--app needs a value' err || fail "--app without a value: rc $rc, $(cat err)"
        mkdir -p elsewhere
        rc=0; DEVENV_ROOT="$PWD/elsewhere" ./frappe-test 2> err || rc=$?
        [ "$rc" = 64 ] && grep -q 'cannot tell which app this is; pass --app' err || fail "no app: rc $rc, $(cat err)"
        rc=0; FRAPPE_SITE= ./frappe-test 2> err || rc=$?
        [ "$rc" = 64 ] && grep -q 'no site: set FRAPPE_SITE or pass --site' err || fail "no site: rc $rc, $(cat err)"
        run 0 "--app" --app fixture --reuse-site --no-coverage --no-composition
        grep -q 'run-tests --app fixture' "$STATE/log" || grep -q 'tests: fixture' run.log || fail "--app: $(cat run.log)"
        echo "ok   --app without a value, no app to be found and no site are exit 64"

        # The prompt for an existing site, on a terminal.
        run_tty 64 "an existing site, answered no" n --no-coverage --no-composition
        grep -q 'Recreate it? \[y/N\]' run.log && grep -q 'kept dev.localhost' run.log || fail "answered no: $(cat run.log)"
        ! grep -q 'provision-site' "$STATE/log" || fail "answered no provisioned: $(cat "$STATE/log")"
        run_tty 0 "an existing site, answered yes" y --no-coverage --no-composition
        grep -qx 'provision-site created dev.localhost' "$STATE/log" || fail "answered yes did not recreate: $(cat "$STATE/log")"
        echo "ok   on a terminal an existing site is recreated on a yes and kept (exit 64) on a no"

        # The bench: brought up and stopped again, unless --keep-up; --down stops it.
        export STUB_PC_DOWN=1 STUB_DEVENV_OK=1
        run 0 "the bench this run started" --reuse-site --no-coverage --no-composition
        grep -q 'starting the bench' run.log && grep -q 'process-compose .* down' "$STATE/log" || fail "not stopped: $(cat "$STATE/log")"
        run 0 "--keep-up" --reuse-site --no-coverage --no-composition --keep-up
        ! grep -q 'process-compose .* down' "$STATE/log" || fail "--keep-up stopped the bench: $(cat "$STATE/log")"
        run 0 "--down" --down
        grep -qx "process-compose -U -u $PC_SOCKET_PATH down" "$STATE/log" || fail "--down: $(cat "$STATE/log")"
        [ ! -e "$STATE/pc-up" ] || fail "--down left the bench up"
        unset STUB_DEVENV_OK
        STUB_PC_DOWN=""
        echo "ok   a bench frappe-test started is stopped at the end, not with --keep-up; --down stops it"

        # Provisioning: the app processes stopped and started again; a failure is exit 10.
        printf 'web\nworker\n' > "$STATE/procs"
        run 0 "provision with app processes" --recreate-site --no-coverage --no-composition
        grep -q 'process stop web' "$STATE/log" && grep -q 'process start worker' "$STATE/log" \
          && ! grep -q 'process stop scheduler' "$STATE/log" || fail "processes: $(cat "$STATE/log")"
        STUB_PROVISION_FAILS=1 run 10 "provision-site fails" --recreate-site --no-coverage --no-composition
        grep -q 'could not prepare dev.localhost' run.log && [ "$(stage site)" = error ] || fail "provision fails: $(cat run.log)"
        grep -q 'process start web' "$STATE/log" || fail "not restarted after a failed provision: $(cat "$STATE/log")"
        rm "$STATE/procs"
        mkdir -p bench/sites/dev.localhost
        echo "ok   provisioning stops the app processes present and starts them again; a failure is exit 10, stage site error"

        # The default setup with erpnext installed. (An unknown setup step never reaches
        # frappe-test: the configuration's schema refuses it, exit 10 above.)
        printf 'frappe\nerpnext\n' > bench/sites/apps.txt
        printf '[project]\nname = "fixture"\n' > repo/pyproject.toml
        run 0 "erpnext in apps.txt" --reuse-site --no-coverage --no-composition
        grep -q 'frappe-test: setup module:erpnext.tests.bootstrap_test_data' run.log \
          && grep -q 'bench --site dev.localhost run-tests --module erpnext.tests.bootstrap_test_data' "$STATE/log" || fail "erpnext setup: $(cat "$STATE/log")"
        rm bench/sites/apps.txt
        echo "ok   with erpnext installed, the default setup is erpnext's bootstrap"

        # The tests: filters, JUnit, a failure, the coverage gate.
        pyproject ""
        run 0 "filters" --reuse-site --no-composition --no-coverage --module M --doctype D --test a --test b
        grep -q 'run-tests --app fixture --module M --doctype D --test a --test b' "$STATE/log" || fail "filters: $(cat "$STATE/log")"
        run 0 "--ci's JUnit" --ci --reuse-site --no-composition --no-coverage
        grep -q -- "--junit-xml-output $PWD/out/junit.xml" "$STATE/log" || fail "--ci's JUnit: $(cat "$STATE/log")"
        run 0 "--junit, relative" --reuse-site --no-composition --no-coverage --junit j.xml
        grep -q -- "--junit-xml-output $PWD/j.xml" "$STATE/log" || fail "--junit: $(cat "$STATE/log")"
        STUB_NO_XMLRUNNER=1 run 10 "--junit without xmlrunner" --reuse-site --no-composition --no-coverage --junit j.xml
        grep -q 'environment has no unittest-xml-reporting' run.log || fail "no xmlrunner: $(cat run.log)"
        STUB_TESTS_RC=1 run 1 "failing tests" --reuse-site --no-composition --no-coverage
        [ "$(stage tests)" = failed ] || fail "failing tests: stage $(stage tests)"
        STUB_COV_REPORT_RC=2 run 2 "below fail_under" --reuse-site --no-composition --no-testmap
        grep -q 'coverage is below \[tool.coverage.report\] fail_under' run.log && [ "$(stage coverage)" = failed ] || fail "fail_under: $(cat run.log)"
        STUB_COV_REPORT_RC=2 run 0 "a filter skips the gate" --reuse-site --no-composition --module M
        grep -q 'a filter is set; the coverage gate is skipped' run.log && [ "$(stage coverage)" = skipped ] && [ "$(stage testmap)" = skipped ] \
          || fail "filtered coverage: $(cat run.log)"
        grep -q -- "-m coverage run --rcfile=$PWD/repo/pyproject.toml --data-file=$PWD/out/.coverage --source=$PWD/repo/fixture -m frappe.utils.bench_helper" "$STATE/log" \
          || fail "coverage run: $(cat "$STATE/log")"
        echo "ok   filters and JUnit reach run-tests; failing tests are exit 1; coverage under fail_under is 2; a filter skips the gate and the testmap"

        # ty and nix-lint, turned on without --ci.
        run 0 "--ty without tools/pyproject.toml" --ty --reuse-site --no-composition --no-coverage
        grep -q 'no tools/pyproject.toml, so no ty' run.log && [ "$(stage ty)" = skipped ] || fail "--ty without tools: $(cat run.log)"
        mkdir -p repo/tools && touch repo/tools/pyproject.toml
        run 5 "--ty with diagnostics" --ty --reuse-site --no-composition --no-coverage
        [ "$(jq -c .ty out/frappe-test-report.json)" = '{"diagnostics":3,"ignores":0}' ] || fail "ty: $(jq -c .ty out/frappe-test-report.json)"
        grep -q 'uv run --frozen --project tools ty check --python' "$STATE/log" || fail "ty's command: $(cat "$STATE/log")"
        rm -r repo/tools
        mkdir -p repo/nix && printf '{a=1;}\n' > repo/nix/bad.nix && git -C repo add nix/bad.nix
        run 6 "--nix-lint on a misformatted file" --nix-lint --reuse-site --no-composition --no-coverage
        [ "$(stage nix-lint)" = failed ] || fail "nix-lint: $(stage nix-lint)"
        git -C repo rm -qf nix/bad.nix
        echo "ok   --ty runs ty (exit 5, its diagnostics in the report), or skips it without tools/pyproject.toml; --nix-lint fails a misformatted file (exit 6)"
        touch "$out"
      '';
}
