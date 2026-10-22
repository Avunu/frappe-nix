# `bench setup requirements`: the script and the wrapper's dispatch to it,
# rendered from lib/scripts.nix, linted, and driven with stubs for the tools
# they call (devenv never shellchecks a `scripts.<n>.exec` body).
{ pkgs, checks }:

let
  inherit (pkgs) lib;
  inherit (import ../lib/standards/outputs.nix { inherit pkgs; }) frappeNixTools;
  render =
    {
      appMode ? false,
      appsWithNode ? [
        "alpha"
        "beta"
      ],
    }:
    import ../lib/scripts.nix {
      inherit
        lib
        pkgs
        appMode
        appsWithNode
        ;
      benchBin = "real-bench";
      nodeVerifyBin = "node-verify";
      nodeModulesBin = "node-modules";
      pythonBin = "python";
      # The Python command the shim execs (frappe-nix-bench, lib/bench-cli.nix).
      benchCli = import ../lib/bench-cli.nix { inherit pkgs frappeNixTools; };
    };
  scripts = render { };
in
{
  setup-requirements =
    checks.script "setup-requirements"
      {
        tools = [
          pkgs.shellcheck
          pkgs.gnused
        ];
        drvArgs = {
          setup = scripts.bench-setup-requirements.exec;
          umbrella = scripts.bench.exec;
          appSetup = (render { appMode = true; }).bench-setup-requirements.exec;
          noNodeSetup = (render { appsWithNode = [ ]; }).bench-setup-requirements.exec;
          passAsFile = [
            "setup"
            "umbrella"
            "appSetup"
            "noNodeSetup"
          ];
        };
      }
      ''
        export HOME="$PWD"
        shellcheck -s bash -S warning -e SC2317 "$setupPath" "$appSetupPath" "$noNodeSetupPath"
        # The umbrella's `cd "$FRAPPE_BENCH_ROOT"` predates this lint.
        shellcheck -s bash -S warning -e SC2317,SC2164 "$umbrellaPath"
        bash ${./setup-requirements.sh} "$setupPath" "$umbrellaPath" "$appSetupPath" "$noNodeSetupPath" 2>&1 | tee "$out"
      '';
}
