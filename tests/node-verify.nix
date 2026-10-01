# `frappe-nix-node-verify` and the Python half of `bench setup requirements`,
# over fixture trees: a yarn cache and node_modules with every kind of damage
# yarn cannot see, and a bench whose requirements are, or are not, in uv.lock.
# Fabricated ELF files stand in for native binaries, so nothing here needs a
# network or a package manager.
{ pkgs }:

{
  node-verify =
    pkgs.runCommand "frappe-nix-node-verify-check"
      {
        nativeBuildInputs = [
          pkgs.git
          pkgs.python3
          pkgs.findutils
        ];
      }
      ''
        export HOME="$PWD"
        bash ${./node-verify.sh} ${import ../lib/node-verify.nix { inherit pkgs; }}/bin/frappe-nix-node-verify 2>&1 | tee "$out"
      '';

  requirements-check =
    pkgs.runCommand "frappe-nix-requirements-check-check"
      {
        nativeBuildInputs = [ pkgs.python3 ];
      }
      ''
        export HOME="$PWD"
        bash ${./requirements-check.sh} ${pkgs.python3}/bin/python3 ${../lib/requirements-check.py} 2>&1 | tee "$out"
      '';
}
