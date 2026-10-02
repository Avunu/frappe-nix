# `bench setup requirements`: the script and the wrapper's dispatch to it,
# rendered from lib/scripts.nix, linted, and driven with stubs for the tools
# they call (devenv never shellchecks a `scripts.<n>.exec` body).
{ pkgs }:

let
  inherit (pkgs) lib;
  scripts = import ../lib/scripts.nix {
    inherit lib pkgs;
    appsWithNode = [
      "alpha"
      "beta"
    ];
    benchBin = "real-bench";
    nodeVerifyBin = "node-verify";
    nodeModulesBin = "node-modules";
    pythonBin = "python";
  };
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
        passAsFile = [
          "setup"
          "umbrella"
        ];
      }
      ''
        export HOME="$PWD"
        shellcheck -s bash -S warning -e SC2317 "$setupPath"
        # The umbrella's `cd "$FRAPPE_BENCH_ROOT"` predates this lint.
        shellcheck -s bash -S warning -e SC2317,SC2164 "$umbrellaPath"
        bash ${./setup-requirements.sh} "$setupPath" "$umbrellaPath" 2>&1 | tee "$out"
      '';
}
