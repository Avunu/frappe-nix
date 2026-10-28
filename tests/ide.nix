# frappe-nix.ide (docs/development/editors.md): what the dev shell gives an
# editor, and that a language server actually resolves a bench's imports with it.
#
#   ide-config    at evaluation, through the real devenv module over
#                 tests/fixtures/ide-bench: the generated pyrightconfig.json's
#                 extraPaths are the shell's PYTHONPATH, relative to the bench
#                 root and in its order; ide.pyrightSettings merges over it; the
#                 language server is on the shell's PATH only when asked for,
#                 nothing is linked with ide.enable off, and ide.vscodeSettings
#                 is on in bench mode and off in app mode (S35). And lib/ide.nix in
#                 app mode, where the bench is generated under app.benchDir and
#                 the app under development is the workspace root itself.
#   ide-resolve   basedpyright over a copy of the fixture bench, with ./.venv
#                 and ./pyrightconfig.json linked as shell entry links them:
#                 one app imports another app's module (extraPaths) and a
#                 package from the dev virtualenv (.venv), with no diagnostics.
#                 Then the same without extraPaths, and without .venv, each of
#                 which has to fail: the virtualenv's editable .pth files are
#                 `import` lines, which a static analyser skips, so the apps
#                 are reachable through extraPaths or not at all.
{
  self,
  pkgs,
  inputs,
}:

let
  inherit (pkgs) lib;
  checks = inputs.python-devops-nix.lib.checks.mk {
    inherit pkgs;
    product = "frappe-nix";
  };
  ideLib = import ../lib/ide.nix { inherit lib; };

  fixture = ./fixtures/ide-bench;

  benchWith =
    frappe-nix:
    import ./standards/fixtures/bench-flake.nix { inherit self pkgs; } (_: {
      frappe-nix = {
        enable = true;
        name = "ide-bench";
        workspaceRoot = fixture;
        # The fixture's requires-python.
        python = pkgs.python314;
        # The rules a missing import or symbol breaks, as errors, so
        # ide-resolve fails on either. Also what shows the merge.
        ide.pyrightSettings = {
          reportMissingImports = "error";
          reportMissingModuleSource = "error";
          reportAttributeAccessIssue = "error";
        };
      }
      // frappe-nix;
    });

  bench = benchWith { };
  withServer = benchWith { ide.languageServer.enable = true; };
  ideOff = benchWith {
    ide.enable = false;
    ide.languageServer.enable = true;
  };

  shell = config: config.devenv.shells.default;
  # The file's content as a value: reading the file itself would be
  # import-from-derivation, which `nix flake check --no-build` refuses.
  pyrightOf = config: config.packages.pyrightConfig.value;
  # Not lib.hasInfix: that is builtins.match ".*X.*", which overflows the C
  # stack on a long enterShell (tests/removed.nix).
  contains = needle: haystack: builtins.replaceStrings [ needle ] [ "" ] haystack != haystack;
  benchRoot = (shell bench).env.FRAPPE_BENCH_ROOT;
  hasServer = config: lib.elem pkgs.basedpyright (shell config).packages;

  generated = pyrightOf bench;
  # The option's value alone: reading it forces nothing else of app mode.
  vscodeIn = frappe-nix: (benchWith frappe-nix).frappe-nix.ide.vscodeSettings;
in
{
  ide-config = checks.eval "ide-config" {
    expected = {
      extraPaths = [
        "apps/erpnext"
        "apps/frappe"
      ];
      pythonPath = map (p: "${benchRoot}/${p}") generated.extraPaths;
      venv = {
        venvPath = ".";
        venv = ".venv";
        pythonVersion = "3.14";
        interpreter = "\${workspaceFolder}/.venv/bin/python";
      };
      typeCheckingMode = "off";
      merged = {
        reportMissingImports = "error";
        reportMissingModuleSource = "error";
        reportAttributeAccessIssue = "error";
      };
      server = {
        default = false;
        enabled = true;
        ideOff = false;
      };
      linked = {
        on = true;
        off = false;
      };
      # On in bench mode, off in app mode (S35), and either can be set.
      vscodeSettings = {
        bench = true;
        app = false;
        appOverridden = true;
        benchOverridden = false;
        seeded = true;
      };
      appMode = [
        ".frappe-nix/bench/apps/frappe"
        ".frappe-nix/bench/apps/erpnext"
      ];
    };
    actual = {
      inherit (generated) extraPaths;
      pythonPath = lib.splitString ":" (shell bench).env.PYTHONPATH;
      venv = {
        inherit (generated) venvPath venv pythonVersion;
        interpreter = ideLib.vscodeSettings."python.defaultInterpreterPath";
      };
      inherit (generated) typeCheckingMode;
      merged = {
        inherit (generated) reportMissingImports reportMissingModuleSource reportAttributeAccessIssue;
      };
      server = {
        default = hasServer bench;
        enabled = hasServer withServer;
        ideOff = hasServer ideOff;
      };
      linked = {
        on = contains "pyrightconfig.json" (shell bench).enterShell;
        off = contains "pyrightconfig.json" (shell ideOff).enterShell;
      };
      vscodeSettings = {
        bench = bench.frappe-nix.ide.vscodeSettings;
        app = vscodeIn { app.enable = true; };
        appOverridden = vscodeIn {
          app.enable = true;
          ide.vscodeSettings = true;
        };
        benchOverridden = vscodeIn { ide.vscodeSettings = false; };
        seeded = contains ".vscode/settings.json" (shell bench).enterShell;
      };
      # frappe first, then the siblings, then the app itself, which is left
      # out: it is the workspace root.
      appMode = ideLib.extraPaths {
        appNames = [
          "frappe"
          "erpnext"
          "my_app"
        ];
        benchDir = ".frappe-nix/bench";
        devApp = "my_app";
      };
    };
  };

  ide-resolve =
    checks.script "ide-resolve"
      {
        src = fixture;
        tools = [
          pkgs.basedpyright
          pkgs.jq
        ];
      }
      ''
        ln -s ${bench.packages.devPythonEnv} .venv
        ln -s ${bench.packages.pyrightConfig} pyrightconfig.json

        echo "── with frappe-nix.ide's links ──"
        basedpyright --outputjson > result.json || true
        jq . result.json
        jq -e '.summary.filesAnalyzed > 0 and .summary.errorCount == 0 and .summary.warningCount == 0' result.json > /dev/null

        # Each control has to fail, or the pass above proves nothing.
        control() {
          echo "── $1 ──"
          if basedpyright > control.txt; then
            cat control.txt
            echo "expected errors $1" >&2
            exit 1
          fi
          cat control.txt
          grep -q "$2" control.txt
        }

        rm pyrightconfig.json
        jq '.extraPaths = []' ${bench.packages.pyrightConfig} > pyrightconfig.json
        control "without extraPaths" '"frappe.utils"'

        jq 'del(.venv, .venvPath)' ${bench.packages.pyrightConfig} > pyrightconfig.json
        rm .venv
        control "without .venv" '"iniconfig"'
      '';
}
