# N1's checks (docs/ironclad/spec.md §7, "N1: runtime"), the parts that need no
# running bench. selftest-runtime.yml runs the rest against the fixture app.
#
#   ironclad-ports           the port offset: the primary checkout's is today's
#                            (a hash of benchName), a linked worktree's differs,
#                            FRAPPE_NIX_PORT_OFFSET parses, and one offset sets
#                            web, db and the three Mailpit ports, through the
#                            real module (evaluated, not the shell).
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
      mail = config.frappe-nix.devguard.mail;
      inherit (config.frappe-nix.ports) offset;
    in
    {
      inherit offset;
      # modules/devenv.nix takes both from basesFor (asserted on its source below).
      inherit (ports.basesFor offset) web db;
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
      lib.hasInfix "webBase = if cfg.ports.base != null then cfg.ports.base else portBases.web;" devenvSource
      && lib.hasInfix "dbBase = portBases.db;" devenvSource
      && lib.hasInfix "portBases = ports.basesFor portOffset;" devenvSource
    ) "devenv.nix: the web or db base no longer comes from ports.basesFor";
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
    assert lib.assertMsg (lib.hasInfix "\${ciMode.exports}" devenvSource)
      "devenv.nix: enterShell lost ciMode.exports";
    assert lib.assertMsg (
      lib.hasInfix "ciMode.unlessCi ''\n" devenvSource
      && lib.hasInfix "frappe-nix-node-verify \"$FRAPPE_BENCH_ROOT\"" devenvSource
    ) "devenv.nix: the node steps are not wrapped in ciMode.unlessCi";
    assert lib.assertMsg (lib.hasInfix "\${ciMode.banner {" devenvSource)
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

  ironclad-frappe-test = pkgs.runCommand "ironclad-frappe-test-check" { } ''
    set -euo pipefail
    cat > frappe-test <<'EOF'
    #!${lib.getExe pkgs.bash}
    ${frappeTest}
    EOF
    chmod +x frappe-test
    ./frappe-test --help | grep -q -- '--reuse-site'
    set +e
    ./frappe-test --no-such-flag 2> err; [ "$?" = 2 ] || { echo "FAIL an unknown flag is not exit 2" >&2; exit 1; }
    env -u FRAPPE_BENCH_ROOT ./frappe-test 2> err; rc=$?
    set -e
    [ "$rc" != 0 ] && grep -q 'app dev shell' err || { echo "FAIL outside a shell: rc $rc, $(cat err)" >&2; exit 1; }
    touch "$out"
  '';
}
