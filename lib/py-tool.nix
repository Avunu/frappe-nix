# A frappe-nix-tools module as a command of its own: `python -I -m <module>`
# from an environment that holds the package, as lib/bench-cli.nix and
# lib/root-sync.nix run theirs. Not the console script, whose wrapper would put
# its own PATH ahead of the dev shell's, and these take yarn and uv from PATH
# on purpose. -I also keeps the bench venv's PYTHONPATH out.
{
  pkgs,
  # The command's name, and the module it runs.
  name,
  module,
  # frappe-nix-tools (lib/standards/outputs.nix).
  frappeNixTools ? (import ./standards/outputs.nix { inherit pkgs; }).frappeNixTools,
}:

let
  python = pkgs.python314.withPackages (_: [ frappeNixTools ]);
in
pkgs.writeShellApplication {
  inherit name;
  text = ''
    exec ${python}/bin/python3 -I -m ${module} "$@"
  '';
}
