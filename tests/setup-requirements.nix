# `bench setup requirements`: the script and the wrapper's dispatch to it,
# rendered from lib/scripts.nix, linted, and driven with stubs for the tools
# they call (devenv never shellchecks a `scripts.<n>.exec` body).
{ pkgs }:

let
  inherit (pkgs) lib;
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
    };
  scripts = render { };
in
{
  setup-requirements =
    pkgs.runCommand "frappe-nix-setup-requirements-check"
      {
        nativeBuildInputs = [
          pkgs.shellcheck
          pkgs.gnused
        ];
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
      }
      ''
        export HOME="$PWD"
        shellcheck -s bash -S warning -e SC2317 "$setupPath"
        # The app-mode and node-less renderings leave a flag variable unread.
        shellcheck -s bash -S warning -e SC2317,SC2034 "$appSetupPath" "$noNodeSetupPath"
        # The umbrella's `cd "$FRAPPE_BENCH_ROOT"` predates this lint.
        shellcheck -s bash -S warning -e SC2317,SC2164 "$umbrellaPath"
        bash ${./setup-requirements.sh} "$setupPath" "$umbrellaPath" "$appSetupPath" "$noNodeSetupPath" 2>&1 | tee "$out"
      '';
}
