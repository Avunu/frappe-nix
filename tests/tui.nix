# `bench ui` (python-devops-nix's lib.tui), over the IDE test's fixture bench
# (tests/ide.nix):
#
#   tui           tests/test_tui.py on the menu the shell builds: its spec, the
#                 store's plugin (lib/tui/frappe_nix_tui) over a fixture
#                 catalog and the shipped one, frappe-nix's command catalog,
#                 and the TUI headless; then the program, which refuses to
#                 start without a terminal, and the `bench` wrapper's `ui`.
{
  self,
  pkgs,
  inputs,
}:

let
  checks = inputs.python-devops-nix.lib.checks.mk {
    inherit pkgs;
    product = "frappe-nix";
  };
  config = import ./standards/fixtures/bench-flake.nix { inherit self pkgs; } (_: {
    frappe-nix = {
      enable = true;
      name = "tui-bench";
      workspaceRoot = ./fixtures/ide-bench;
      python = pkgs.python314;
    };
  });
  run = config.frappe-nix.tui;
  umbrella = config.devenv.shells.default.scripts.bench.exec;
  # Not lib.hasInfix: builtins.match overflows on a long script (tests/removed.nix).
  contains = needle: haystack: builtins.replaceStrings [ needle ] [ "" ] haystack != haystack;
in
{
  tui = checks.script "tui" { } ''
    ${run.tui.python}/bin/python3 -I ${./test_tui.py} ${run.tui.modules} ${run.spec}
    if ${pkgs.lib.getExe run} < /dev/null 2> err; then
      echo "the menu started without a terminal" >&2
      exit 1
    fi
    grep -qF 'the menu needs a terminal. Run `bench --help`' err
    ${
      if contains "ui)          shift; exec ${pkgs.lib.getExe run}" umbrella then
        "echo 'bench ui runs the menu'"
      else
        "echo 'bench ui does not run the menu' >&2; exit 1"
    }
  '';
}
