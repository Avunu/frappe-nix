# `bench-uninstall-app`, rendered from lib/scripts.nix and driven against
# file:// remotes and a stub bench: the inverse of tests/bench-get-app.nix —
# what gets torn out of .gitmodules, pyproject.toml and sites/, and what is
# correctly left alone when another site still has the app installed.
{ pkgs }:

let
  inherit (pkgs) lib;

  scripts = import ../lib/scripts.nix {
    inherit lib pkgs;
    appsWithNode = [ ];
    benchBin = "bench";
  };
in
{
  bench-uninstall-app =
    pkgs.runCommand "frappe-nix-bench-uninstall-app-check"
      {
        nativeBuildInputs = with pkgs; [
          git
          jq
          coreutils
          python3
          shellcheck
        ];
        script = scripts.bench-uninstall-app.exec;
        # The umbrella dispatcher: bench-uninstall-app.sh also checks that
        # `bench --site <name> uninstall-app <app>` — the idiomatic form
        # Frappe's own docs show — reaches bench-uninstall-app rather than
        # silently falling through to the raw, teardown-free command, since
        # the dispatch only matches $1 against a bare subcommand name.
        dispatch = scripts.bench.exec;
        passAsFile = [
          "script"
          "dispatch"
        ];
      }
      ''
        export HOME="$PWD"
        shellcheck -s bash -S warning -e SC2317 "$scriptPath"
        bash ${./bench-uninstall-app.sh} "$scriptPath" "$dispatchPath" | tee "$out"
      '';
}
