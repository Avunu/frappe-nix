# Finds and repairs a damaged yarn cache or node_modules — see node-verify.py,
# which says what damage looks like and why yarn never notices it itself.
#
# Runs on every shell entry, before the install (lib/node-modules.nix), so what
# it removes is what that install puts back; and from `bench setup
# requirements`, which reports and repairs on demand. Stdlib Python, no
# dependencies: it has to work on the shell entry that installs them.
{ pkgs }:

pkgs.writeShellApplication {
  name = "frappe-nix-node-verify";
  runtimeInputs = [
    pkgs.git
    pkgs.python3
  ];
  text = ''
    exec python3 ${./node-verify.py} "$@"
  '';
}
