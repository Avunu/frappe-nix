# frappe_devguard (PLAN.md, Phase 3; python-devops-nix's CONVENTIONS.md, "The
# core and the product's package"):
#
#   devguard             lib/devguard/tests/test_devguard.py against the package
#                        lib/grafts.nix builds, grafted into a virtualenv by
#                        lib.python.withGrafts, on every interpreter
#                        lib/frappe-presets.json names: the .pth name, the
#                        frappe_devguard[<guard>] log lines, _product.py,
#                        _frappe.py and the Frappe guards.
#   devguard-core        python-devops-nix's own suite for the core, against its
#                        stub product, on the same interpreters.
#   devguard-production  design rule 7: the production virtualenv a bench builds
#                        (prodPythonEnv, which builtBench, the containers and
#                        services.frappe-nix run) carries no frappe_devguard and no
#                        devguard .pth, while the development one does. Through
#                        the real devenv module, over a fixture workspace.
#   grafts               every graft lib/grafts.nix builds carries the shared
#                        _hook.py, and the bench's development and production
#                        interpreters start with every .pth graft importing
#                        cleanly (a graft built without its _hook.py fails
#                        each interpreter start with "Error processing line").
#   devguard-renames     the Track B renames (devguard.mail.smtpPort, now mailpit.port; the
#                        devguard.scheduler tree): the old names still set the
#                        new options and the baked settings, and are listed for
#                        the evaluation warning. At evaluation; nothing is built.
#   devguard-test-relax  [tool.frappe-nix.tests] devguard-disable (profile.schema.json)
#                        accepts every guard the devenv module's devguard tree has
#                        except egress, and nothing else, so a guard added to the
#                        tree or renamed cannot drift from what an app may relax
#                        for its tests. At evaluation; nothing is built.
{
  self,
  pkgs,
  inputs,
}:

let
  inherit (pkgs) lib;
  base = inputs.python-devops-nix.lib;
  grafts = import ../lib/grafts.nix {
    inherit pkgs;
    inherit (inputs) python-devops-nix;
  };
  checks = base.checks.mk {
    inherit pkgs;
    product = "frappe-nix";
  };

  interpreters = base.python.interpreters { presets = [ ../lib/frappe-presets.json ]; };

  # Baked values the suite looks for, as devenv.nix's devguardConfig would bake
  # them.
  package = grafts.devguard {
    enabled = true;
    guards = {
      mail.port = 2525;
      jobs.extra_blocked = [ "baked.tasks.push" ];
    };
  };

  run =
    attr:
    let
      python = pkgs.${attr};
      # What mkVirtualEnv leaves for withGrafts: a site-packages directory
      # created by the install phase.
      env = pkgs.stdenvNoCC.mkDerivation {
        name = "frappe-devguard-venv-${attr}";
        dontUnpack = true;
        dontBuild = true;
        installPhase = ''
          runHook preInstall
          mkdir -p "$out/${python.sitePackages}"
          runHook postInstall
        '';
      };
      grafted = base.python.withGrafts {
        inherit python env;
        grafts = [ package ];
      };
    in
    ''
      echo "== ${attr} (${python.version})"
      ${python.interpreter} -I ${../lib/devguard/tests/test_devguard.py} ${grafted}/${python.sitePackages} 2>&1
    '';

  # A bench through the real module: tests/standards/fixtures/bench-flake.nix.
  benchWith =
    extra:
    import ./standards/fixtures/bench-flake.nix
      {
        inherit self pkgs;
      }
      (_: {
        frappe-nix = {
          enable = true;
          name = "devguard-production";
          workspaceRoot = ./standards/fixtures/bench-dev-group;
          # The fixture's requires-python.
          python = pkgs.python314;
        }
        // extra;
      });
  bench = benchWith { };
  renamed = benchWith {
    devguard.mail.smtpPort = 2999;
    devguard.scheduler = {
      enable = false;
      blockServerScripts = false;
      extraBlockedJobs = [ "myapp.tasks.push" ];
    };
  };
  inherit (bench.packages) prodPythonEnv devPythonEnv builtBench;
  sitePackages = bench.frappe-nix.python.sitePackages;
in
{
  devguard =
    checks.script "devguard" { }
      "{ ${lib.concatMapStrings run interpreters} } | tee \"$out\"";

  devguard-core = base.devguard.check {
    inherit pkgs interpreters;
    product = "frappe-nix";
    name = "devguard-core";
  };

  grafts = checks.script "grafts" { } ''
    ${lib.concatMapStrings
      (name: ''
        test -f ${grafts.${name}}/${grafts.${name}.module}/_hook.py
        cmp ${grafts.${name}}/${grafts.${name}.module}/_hook.py ${base.python.hook}
        echo "ok   ${grafts.${name}.module} carries the shared _hook.py"
      '')
      [
        "unixsock"
        "journald"
        "nodebuild"
        "benchcli"
      ]
    }
    test -f ${package}/frappe_devguard/_hook.py
    echo "ok   frappe_devguard carries the core's _hook.py"

    for env in ${devPythonEnv} ${prodPythonEnv}; do
      site="$env/${sitePackages}"
      for pth in "$site"/zzz-*.pth; do
        module=$(sed -n 's/^import \([a-z_]*\);.*/\1/p' "$pth")
        [ -n "$module" ] || { echo "FAIL $pth is not an import line" >&2; exit 1; }
        "$env/bin/python" -c "import $module, $module._hook" 2> err || {
          cat err >&2
          echo "FAIL $module does not import from $env" >&2
          exit 1
        }
        if grep -q 'Error processing' err; then
          cat err >&2
          echo "FAIL a .pth failed at interpreter start in $env" >&2
          exit 1
        fi
        echo "ok   $(basename "$pth") imports $module and its _hook ($(basename "$env"))"
      done
    done
  '';

  devguard-renames = checks.eval "devguard-renames" {
    expected = {
      port = 2999;
      jobs = {
        enable = false;
        blockServerScripts = false;
        extraBlocked = [ "myapp.tasks.push" ];
      };
      deprecated = [
        "frappe-nix.devguard.mail.smtpPort"
        "frappe-nix.devguard.scheduler.blockServerScripts"
        "frappe-nix.devguard.scheduler.enable"
        "frappe-nix.devguard.scheduler.extraBlockedJobs"
      ];
      shellVariable = "1";
    };
    actual = {
      inherit (renamed.frappe-nix.mailpit) port;
      inherit (renamed.frappe-nix.devguard) jobs;
      deprecated = lib.sort builtins.lessThan (
        map (
          message: builtins.head (builtins.match "The option `([^']*)'.*" message)
        ) renamed.frappe-nix.deprecations
      );
      shellVariable = bench.devenv.shells.default.env.FRAPPE_NIX_DEVGUARD;
    };
  };

  devguard-test-relax =
    let
      schema = lib.importJSON ../py/frappe_nix_tools/frappe_nix_tools/data/schema/profile.schema.json;
      relaxable = schema."$defs".modules.tests.properties.devguard-disable.items.enum;
      # Every guard in the tree, less the switch for all of them and the alias
      # tree the Track B rename keeps until 2.0.0 (devguard.scheduler, now jobs).
      guards = lib.attrNames (
        lib.filterAttrs (
          name: value: builtins.isAttrs value && value ? enable && name != "scheduler"
        ) bench.frappe-nix.devguard
      );
    in
    checks.eval "devguard-test-relax" {
      message = "devguard-disable's enum in profile.schema.json is the devguard tree's guards, less egress";
      expected = lib.sort builtins.lessThan (lib.remove "egress" guards);
      actual = lib.sort builtins.lessThan relaxable;
    };

  devguard-production =
    assert lib.assertMsg (
      builtBench.passthru.pythonEnv.outPath == prodPythonEnv.outPath
    ) "devguard-production: builtBench no longer runs prodPythonEnv; check its environment instead";
    checks.script "devguard-production" { } ''
      prod=${prodPythonEnv}/${sitePackages}
      dev=${devPythonEnv}/${sitePackages}

      # The positive control: the development environment has it, so the
      # absence below is not a test of the wrong path.
      test -f "$dev/frappe_devguard/_product.py"
      test -f "$dev/zzz-frappe-devguard.pth"
      echo "ok   the development virtualenv carries frappe_devguard and zzz-frappe-devguard.pth"

      # And the production one was grafted: the guard is not missing because
      # nothing was.
      for graft in unixsock journald nodebuild; do
        test -f "$prod/zzz-frappe-$graft.pth"
      done
      echo "ok   the production virtualenv carries the unixsock, journald and nodebuild grafts"

      found=$(find -L "$prod" -maxdepth 2 -iname '*devguard*' -print)
      if [ -n "$found" ]; then
        echo "FAIL the production virtualenv carries devguard:" >&2
        echo "$found" >&2
        exit 1
      fi
      if grep -l devguard "$prod"/*.pth; then
        echo "FAIL a production .pth file names devguard" >&2
        exit 1
      fi
      test ! -e "$prod/frappe_benchcli"
      echo "ok   the production virtualenv (prodPythonEnv, builtBench's) carries no frappe_devguard and no devguard .pth"
    '';
}
