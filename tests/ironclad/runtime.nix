# N1's checks (docs/ironclad/spec.md §7, "N1: runtime"), the parts that need no
# running bench. selftest-runtime.yml runs the rest against the fixture app.
#
#   ironclad-ports           the port offset: the primary checkout's is today's
#                            (a hash of benchName), a linked worktree's differs,
#                            FRAPPE_NIX_PORT_OFFSET parses and wins, and one
#                            offset sets web, db and the three Mailpit ports,
#                            through the real module (evaluated, not the shell).
#   ironclad-ci-mode         FRAPPE_NIX_CI's enterShell snippets, rendered and
#                            run: in CI mode no node verify, no node_modules, a
#                            one-line banner and the two exports; outside it the
#                            interactive path; and modules/devenv.nix wires them.
#   ironclad-bench-dev-group the generated root's dev group has coverage and
#                            none of ruff, pre-commit or semgrep, also after
#                            ensure-root reconciles a root that had them.
#   ironclad-coverage-env    `import coverage` (and xmlrunner) works in the dev
#                            virtualenv lib/python.nix builds from that group.
#   ironclad-frappe-test     frappe-test builds (shellcheck) and parses its
#                            arguments.
{
  pkgs,
  lib,
  self,
  inputs,
  ...
}:

let
  ports = import ../../lib/ports.nix { inherit lib; };
  ciMode = import ../../lib/ci-mode.nix { inherit lib; };

  # --- ports, through the module ----------------------------------------------

  benchFlake = import ./fixtures/bench-flake.nix { inherit self pkgs; };

  portsOf =
    extra:
    let
      config = benchFlake (_: {
        frappe-nix = {
          enable = true;
          benchName = "carbon-frappe";
          workspaceRoot = ../fixtures/lock-audit;
        }
        // extra;
      });
      shell = config.devenv.shells.default;
      mail = config.frappe-nix.devguard.mail;
    in
    {
      inherit (config.frappe-nix.ports) offset;
      web = shell.processes.nginx.ports.main.allocate;
      db = shell.processes.mysql.ports.main.allocate;
      smtp = mail.smtpPort;
      http = mail.httpPort;
      pop3 = mail.pop3.port;
    };

  today = portsOf { };
  set123 = portsOf { ports.offset = 123; };

  seed =
    args:
    ports.seed (
      {
        benchName = "ironclad-fixture";
        appMode = true;
        pwd = "/src/ironclad-fixture";
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
    assert lib.assertMsg (
      contains "webBase = if cfg.ports.base != null then cfg.ports.base else portBases.web;" devenvSource
      && contains "dbBase = portBases.db;" devenvSource
      && contains "portBases = ports.basesFor portOffset;" devenvSource
    ) "devenv.nix: the web or db base no longer comes from ports.basesFor";
    assert lib.assertMsg (
      contains "default = 19000 + portOffsetOf config.frappe-nix;" devenvSource
      && contains "default = 20000 + portOffsetOf config.frappe-nix;" devenvSource
      && contains "default = 21000 + portOffsetOf config.frappe-nix;" devenvSource
      && contains "portOffsetOf = fcfg: ports.effectiveOffset envPortOffset fcfg.ports.offset;" devenvSource
      && contains "portOffset = portOffsetOf cfg;" devenvSource
    ) "devenv.nix: the Mailpit defaults or the bases no longer derive from the one offset";
    # The values every bench had before this change: carbon-frappe served on 8691.
    assert lib.assertMsg (
      ports.offsetFor "carbon-frappe" == 691
    ) "offsetFor changed: carbon-frappe is no longer 691";
    assert lib.assertMsg (
      today == {
        offset = 691;
        web = 8691;
        db = 3997;
        smtp = 19691;
        http = 20691;
        pop3 = 21691;
      }
    ) "a bench's default ports moved: ${builtins.toJSON today}";
    assert lib.assertMsg (
      set123 == {
        offset = 123;
        web = 8123;
        db = 3429;
        smtp = 19123;
        http = 20123;
        pop3 = 21123;
      }
    ) "ports.offset = 123 does not set every port: ${builtins.toJSON set123}";
    # The primary checkout (.git is a directory) keeps the bench-name hash.
    assert lib.assertMsg (seed { } == "ironclad-fixture") "the primary checkout's seed is salted";
    assert lib.assertMsg (
      seed {
        pwd = "";
        gitKind = null;
      } == "ironclad-fixture"
    ) "pure evaluation salts the seed";
    assert lib.assertMsg (
      seed {
        appMode = false;
        gitKind = "regular";
      } == "ironclad-fixture"
    ) "bench mode salts the seed (its common_site_config.json is committed)";
    assert lib.assertMsg (
      seed { gitKind = "regular"; } == "ironclad-fixture@/src/ironclad-fixture"
    ) "a linked worktree's seed is not salted with its path";
    assert lib.assertMsg (
      worktreeA != worktreeB && worktreeA != ports.offsetFor "ironclad-fixture"
    ) "two worktrees of one app share an offset (${toString worktreeA}, ${toString worktreeB})";
    assert lib.assertMsg (ports.parseEnvOffset "123" == 123) "FRAPPE_NIX_PORT_OFFSET=123 is not 123";
    assert lib.assertMsg (ports.parseEnvOffset "007" == 7) "FRAPPE_NIX_PORT_OFFSET=007 is not 7";
    assert lib.assertMsg (ports.parseEnvOffset "0" == 0) "FRAPPE_NIX_PORT_OFFSET=0 is not 0";
    assert lib.assertMsg (
      ports.parseEnvOffset "" == null
    ) "an unset FRAPPE_NIX_PORT_OFFSET is not null";
    assert lib.assertMsg (ports.effectiveOffset 123 691 == 123) "FRAPPE_NIX_PORT_OFFSET does not win";
    assert lib.assertMsg (
      ports.effectiveOffset null 691 == 691
    ) "the option is ignored without the variable";
    assert lib.assertMsg (
      ports.basesFor 123 == {
        web = 8123;
        db = 3429;
        socketio = 9123;
        mailSmtp = 19123;
        mailHttp = 20123;
        mailPop3 = 21123;
      }
    ) "basesFor 123 is wrong";
    {
      inherit today set123;
      worktrees = [
        worktreeA
        worktreeB
      ];
      primary = ports.offsetFor "ironclad-fixture";
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
      benchName = "ironclad-fixture";
      interactive = "echo interactive-banner";
    }}
    echo "DEVENV_IN_DIRENV_SHELL=''${DEVENV_IN_DIRENV_SHELL:-unset} PC_TUI_ENABLED=''${PC_TUI_ENABLED:-unset}"
  '';

  # --- the bench root's dev group ---------------------------------------------

  template = builtins.fromTOML (builtins.readFile ../../templates/bench/pyproject.toml);
  templateDev = template.dependency-groups.dev;
  devGroupFixture = ./fixtures/bench-dev-group;
  fixtureDev =
    (builtins.fromTOML (builtins.readFile (devGroupFixture + "/pyproject.toml"))).dependency-groups.dev;
  envs = import ../../lib/python.nix {
    inherit pkgs lib;
    python = pkgs.python314;
    workspaceRoot = devGroupFixture;
    benchName = "ironclad-dev-group";
    inherit (inputs) pyproject-nix pyproject-build-systems uv2nix;
    lockAuditRelock = ''
      tests/ironclad/fixtures/bench-dev-group no longer matches its uv.lock:

          uv lock --project tests/ironclad/fixtures/bench-dev-group
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
  ironclad-ports = pkgs.writeText "ironclad-ports.json" (builtins.toJSON portFacts);

  ironclad-ci-mode =
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
    pkgs.runCommand "ironclad-ci-mode-check" { } ''
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
      grep -qx 'frappe-nix: CI mode (ironclad-fixture, site fixture.localhost)' <<< "$ci" || fail "CI mode banner"
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

  ironclad-bench-dev-group =
    assert lib.assertMsg (fixtureDev == templateDev)
      "tests/ironclad/fixtures/bench-dev-group's dev group is not the template's: re-copy it and re-lock";
    pkgs.runCommand "ironclad-bench-dev-group-check"
      {
        nativeBuildInputs = [
          workspaceTool
          pkgs.python3
        ];
      }
      ''
        set -euo pipefail
        dev() { python3 -c 'import sys, tomllib; print(" ".join(tomllib.load(open(sys.argv[1], "rb"))["dependency-groups"]["dev"]))' "$1"; }
        names() { tr ' ' '\n' | sed -E 's/[<>=!~ ].*//' | sort | tr '\n' ' '; }
        check() { # <label> <dev list>
          local got
          got="$(names <<< "$2")"
          case " $got " in *" coverage "*) ;; *) echo "FAIL $1: no coverage in [$2]" >&2; exit 1 ;; esac
          for bad in ruff pre-commit semgrep; do
            case " $got " in *" $bad "*) echo "FAIL $1: $bad in [$2]" >&2; exit 1 ;; esac
          done
          echo "ok   $1: $2"
        }

        check "the template's dev group" "$(dev ${../../templates/bench/pyproject.toml})"

        cat > old.toml <<'EOF'
        [project]
        name = "old-bench"
        version = "0.1.0"
        requires-python = ">=3.14"
        dependencies = ["frappe-bench>=5.29.0", "frappe-runtime", "setuptools"]

        [dependency-groups]
        dev = ["pre-commit>=4.5.1", "pydantic>=2.12.5", "pytest>=9.0.2", "responses", "ruff>=0.15.0", "semgrep", "mypy"]

        [tool.uv]
        package = false
        EOF
        frappe-nix-workspace ensure-root --pyproject old.toml --template ${../../templates/bench/pyproject.toml}
        check "a root that had ruff, pre-commit and semgrep, after ensure-root" "$(dev old.toml)"
        case " $(dev old.toml) " in *" mypy "*) ;; *) echo "FAIL ensure-root dropped the bench's own mypy" >&2; exit 1 ;; esac
        cp old.toml before.toml
        frappe-nix-workspace ensure-root --pyproject old.toml --template ${../../templates/bench/pyproject.toml}
        cmp -s old.toml before.toml || { echo "FAIL a second ensure-root changed the file" >&2; exit 1; }
        echo "ok   a second ensure-root changes nothing"
        touch "$out"
      '';

  ironclad-coverage-env = pkgs.runCommand "ironclad-coverage-env-check" { } ''
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

  ironclad-frappe-test =
    pkgs.runCommand "ironclad-frappe-test-check"
      {
        nativeBuildInputs = [
          pkgs.git
          pkgs.python3
          pkgs.curl
          pkgs.coreutils
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

        # A dev shell of stubs: the bench answers, every call is logged.
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
        stub stubs/devenv 'echo "frappe-nix: port already in use: nginx=8000; set FRAPPE_NIX_PORT_OFFSET" >&2; exit 1'
        stub stubs/process-compose 'case "$*" in *" process list") [ -z "$STUB_PC_DOWN" ] ;; *" process get "*) exit 1 ;; esac'
        stub stubs/mariadb-admin 'exit 0'
        stub stubs/provision-site 'echo "provision-site created $FRAPPE_SITE" >> "$STATE/log"; mkdir -p "$FRAPPE_BENCH_ROOT/sites/$FRAPPE_SITE"'
        stub stubs/ironclad 'case "$1 $2" in
          "config $STUB_CONFIG_FAIL") exit 2 ;;
          "config test.setup") echo "execute:fixture.setup" ;;
          "config shell-checks") [ -z "$STUB_CHECK" ] || echo "$STUB_CHECK" ;;
        esac'
        stub bench/env/bin/bench 'exit 0'
        stub bench/env/bin/python 'exit 0'

        git init -q repo
        printf '[project]\nname = "fixture"\n' > repo/pyproject.toml
        echo gen/ > repo/.gitignore
        mkdir repo/gen && echo v1 > repo/gen/types.ts
        git -C repo add pyproject.toml .gitignore && git -C repo add -f gen/types.ts && git -C repo commit -qm fixture
        ln -s "$PWD/repo" bench/apps/fixture

        export PATH="$PWD/stubs:$PATH" FRAPPE_BENCH_ROOT="$PWD/bench" DEVENV_ROOT="$PWD/repo" \
          FRAPPE_SITE=dev.localhost PC_SOCKET_PATH="$PWD/pc.sock" FRAPPE_DB_SOCKET=/nowhere \
          STUB_PC_DOWN="" STUB_CONFIG_FAIL="" STUB_CHECK=""
        run() { # <expected exit> <label> <args…>
          local want="$1" label="$2" rc=0
          shift 2
          : > "$STATE/log"
          timeout 60 ./frappe-test --no-coverage --out "$PWD/out" "$@" > run.log 2>&1 || rc=$?
          [ "$rc" = "$want" ] || { cat run.log "$STATE/log"; fail "$label: exit $rc, want $want"; }
        }

        began="$SECONDS"
        STUB_PC_DOWN=1 run 10 "devenv up fails" --reuse-site
        [ $((SECONDS - began)) -lt 30 ] || fail "a failed devenv up waited"
        grep -q 'FRAPPE_NIX_PORT_OFFSET' run.log && grep -q 'devenv up -D failed' run.log || fail "devenv up's message is lost: $(cat run.log)"
        grep -q 'test-report .*--stage up=error' "$STATE/log" || fail "no report with up=error: $(cat "$STATE/log")"
        ! grep -q 'mariadb-admin' "$STATE/log" || fail "it waited for the database after devenv up failed"
        echo "ok   a failed devenv up -D exits 10 at once, with devenv's message"

        run 0 "--site other.localhost" --site other.localhost
        grep -qx 'provision-site created other.localhost' "$STATE/log" || fail "--site: provisioned $(grep created "$STATE/log")"
        grep -q 'bench --site other.localhost set-config allow_tests true' "$STATE/log" || fail "--site: allow_tests on the wrong site"
        grep -q 'bench --site other.localhost execute fixture.setup' "$STATE/log" || fail "--site: setup on the wrong site"
        echo "ok   --site S provisions S, not \$FRAPPE_SITE"

        STUB_CONFIG_FAIL=test.setup run 10 "test.setup unreadable" --reuse-site
        ! grep -q 'run-tests' "$STATE/log" || fail "the tests ran with an unreadable setup"
        echo "ok   an unreadable [tool.ironclad.test] setup fails stage 2 (exit 10)"

        STUB_CONFIG_FAIL=shell-checks run 7 "shell-checks unreadable" --reuse-site --shell-checks
        grep -q 'shell-checks=error' "$STATE/log" || fail "shell-checks stage: $(cat "$STATE/log")"
        echo "ok   unreadable shell-checks fail stage 8b (exit 7)"

        STUB_CHECK='echo v1 > gen/types.ts; echo scratch > gen/other' run 0 "a clean shell check" --reuse-site --shell-checks
        STUB_CHECK='echo v2 > gen/types.ts' run 7 "a stale committed generator" --reuse-site --shell-checks
        grep -q 'gen/types.ts' out/shell-checks.log || fail "the changed file is not listed: $(cat out/shell-checks.log)"
        git -C repo checkout -q gen/types.ts
        echo "ok   a shell check that rewrites a tracked file under an ignored path fails (exit 7)"
        touch "$out"
      '';
}
