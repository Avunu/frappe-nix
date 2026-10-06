# pt-online-schema-change, runnable from anywhere — what lib/offline-migrate.py
# alters large tables with, handed to it through FRAPPE_OFFLINE_MIGRATE_PT_OSC.
#
# nixpkgs' percona-toolkit scripts start with `#!/usr/bin/env perl` and nothing
# puts perl on the PATH of a systemd unit or a shell that did not ask for it, so
# the bare tool dies on `env: 'perl': No such file or directory`. Wrapping it
# with perl beside it makes the store path the whole dependency.
{ pkgs }:

pkgs.writeShellApplication {
  name = "frappe-nix-pt-osc";
  runtimeInputs = [
    pkgs.perl
    pkgs.percona-toolkit
  ];
  text = ''
    exec pt-online-schema-change "$@"
  '';
}
