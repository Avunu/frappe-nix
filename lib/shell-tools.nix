# The dev shell's pieces that come from python-devops-nix,
# configured for frappe-nix in one place: modules/devenv.nix, the scaffolder
# (lib/init.nix), the bench commands (lib/bench-cli.nix) and the flake's checks
# all take them from here.
#
#   ciMode               lib.ciMode: FRAPPE_NIX_CI's enterShell snippets. In CI
#                        mode the banner is one line, "frappe-nix: CI mode
#                        (<name>, site <FRAPPE_SITE>)".
#   frappe-nix-sources   lib.sources.command: shell entry's app checkout
#                        (`enter ROOT`: a fresh clone's apps/* submodules,
#                        once, as partial clones of the paired branch; a report
#                        on the rest) and the first clone of an app (`clone`).
#                        Its scope is apps/, and a checkout does not recurse:
#                        Frappe apps ship nested submodules with broken refs
#                        that have no role in production.
#   frappe-nix-bootstrap lib/bootstrap.sh: frappe-nix-sources from the
#                        revision a bench's flake.lock pins, run before Nix
#                        evaluates the bench's flake, which reads apps/*. A
#                        bench's .envrc runs it on a fresh clone
#                        (`nix run github:Avunu/frappe-nix/release-1#bootstrap`).
#   frappe-nix-db-nocow  lib.dbNocow: the dev MariaDB datadir off btrfs
#                        copy-on-write, created and marked before the server
#                        writes to it; a running server is a process with
#                        --datadir=<dir> (the mariadbd reaper's own test).
#   frappe-nix-banner    lib.banner.command: the welcome banner, from a JSON
#                        spec (lib.banner.script).
#
#   import ./lib/shell-tools.nix { inherit pkgs; base = inputs.python-devops-nix.lib; python = cfg.python; }
{
  pkgs,
  base,
  # The tools' interpreter (python-devops-nix docs/library.md, Python versions:
  # out-of-process tools). The dev shell passes the bench's, so the shell
  # carries no second one; frappe-init and the bench commands, frappe-nix's tool
  # interpreter.
  python ? pkgs.python3,
}:
rec {
  # The CI banner names the site, read when the shell starts so one set in
  # .env is the one named.
  ciMode =
    let
      ciMode = base.ciMode {
        label = "frappe-nix";
        envPrefix = "FRAPPE";
      };
    in
    ciMode
    // {
      banner = args: ciMode.banner ({ details = "site \${FRAPPE_SITE:-unset}"; } // args);
    };

  sources = base.sources.command {
    inherit pkgs python;
    label = "frappe-nix";
    noun = "bench";
    scope = [ "apps" ];
    updateCommand = "bench update --pull";
    removeCommand = "bench remove-app {base}";
    nestedHint = [
      "  'nix build' will not see it. Vendor it (frappe-init --migrate) or push it"
      "  and re-add it with bench-get-app; see README, 'Local apps'."
    ];
  };

  bootstrap = pkgs.writeShellApplication {
    name = "frappe-nix-bootstrap";
    runtimeInputs = [
      pkgs.git
      pkgs.gnugrep
      pkgs.coreutils
    ];
    text = ''
      LABEL=frappe-nix
      INPUT=frappe-nix
      SOURCES=${pkgs.lib.getExe sources}
      LOCKREF=${./bootstrap-lock.nix}
      STATUS="-- apps"
      TREELESS=
    ''
    + builtins.readFile ./bootstrap.sh;
    meta.description = "Check out a fresh clone's apps before Nix evaluates the bench's flake";
  };

  dbNocow = base.dbNocow {
    inherit pkgs;
    label = "frappe-nix";
    strategy = "datadir";
    server = "cmdline";
  };

  banner = base.banner.command {
    inherit pkgs python;
    name = "frappe-nix-banner";
  };
}
