# CI mode for the dev shell (docs/app-standards/spec.md §5.12, S16): what
# modules/devenv.nix's enterShell does differently when FRAPPE_NIX_CI is set.
#
# Read when enterShell runs, not at evaluation, so one shell derivation serves
# both. A CI job enters the shell once (`nix develop --no-pure-eval -c
# frappe-test --ci`) and needs neither node_modules (the tests, ty and `bench
# run-tests` do not touch them; `bench build` and the screenshots do, and those
# jobs leave the flag unset) nor the banner. The node steps cost about two
# minutes of yarn per entry on a cold runner.
#
# Kept apart from the module so tests/standards/runtime.nix can render and run
# the snippets without evaluating a whole dev shell, which needs a relocked app
# and import-from-derivation.
{ lib }:

rec {
  # True in CI mode: FRAPPE_NIX_CI is set to anything but "", "0" or "false".
  isCi = ''case "''${FRAPPE_NIX_CI:-}" in "" | 0 | false) false ;; *) true ;; esac'';

  # The first thing enterShell does in CI mode: `devenv up` from a script needs
  # these (the flake-compat wrapper otherwise re-enters the shell, and
  # process-compose's TUI has no terminal to draw on).
  exports = ''
    if ${isCi}; then
      export DEVENV_IN_DIRENV_SHELL=true PC_TUI_ENABLED=0
    fi
  '';

  # `body` (shell) only outside CI mode: the node_modules verify and install,
  # and the bench-mode apps report.
  unlessCi = body: ''
    if ! ${isCi}; then
      :
      ${body}
    fi
  '';

  # The banner, or in CI mode one line naming the bench and the site.
  # The site is read when the shell starts, so one set in .env is the one named.
  banner =
    { benchName, interactive }:
    ''
      if ${isCi}; then
        echo "frappe-nix: CI mode (${
          lib.escape [ "\"" "$" "`" "\\" ] benchName
        }, site ''${FRAPPE_SITE:-unset})"
      else
        ${interactive}
      fi
    '';
}
