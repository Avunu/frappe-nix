# `bench ui` (python-devops-nix's lib.tui), over the IDE test's fixture bench
# (tests/ide.nix):
#
#   tui           tests/test_tui.py on the menu the shell builds: its spec, the
#                 store's plugin (lib/tui/frappe_nix_tui) over a fixture
#                 catalog and the shipped one, frappe-nix's command catalog,
#                 and the TUI headless; then the program, which refuses to
#                 start without a terminal, with the tabs or the store alone, and
#                 the `bench` wrapper's `ui` and bare `install-app`.
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
  # The wrapper in an app repository, which has no store.
  appWrapper =
    (import ../lib/scripts.nix {
      inherit (pkgs) lib;
      inherit pkgs;
      base = inputs.python-devops-nix.lib;
      appMode = true;
      appsWithNode = [ ];
      tui = "/menu";
    }).bench.exec;
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
    ${
      if contains "then exec ${pkgs.lib.getExe run} --only store; fi" umbrella then
        "echo 'a bare bench install-app opens the store'"
      else
        "echo 'a bare bench install-app does not open the store' >&2; exit 1"
    }
    ${
      if contains "--only store" appWrapper then
        "echo 'an app repository opens a store it does not have' >&2; exit 1"
      else
        "echo 'in an app repository, bench install-app stays the real one'"
    }
    # The store alone is there (no "has no store here"), and needs a terminal.
    if ${pkgs.lib.getExe run} --only store < /dev/null 2> err; then
      echo "the store started without a terminal" >&2
      exit 1
    fi
    grep -qF 'the menu needs a terminal' err
  '';
}
