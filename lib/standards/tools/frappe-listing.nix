# `frappe-listing` = `frappe-nix listing` (docs/app-standards/spec.md §5.2): registry
# readiness (L1 to L12), the registry pull request and the README blocks. A wrapper around
# frappe-nix-tools' bin/frappe-nix, like tools/frappe-nix.nix, with what the rules call
# appended to PATH (so the app's own copies win): git and gh (the registry pull request)
# and uv (L9 builds pilot's validation bench with it). L7's semgrep is the app's own, from
# its tools/ project (the semgrep module pins it there), else `uvx` at the floor version.
{
  pkgs,
  lib,
  frappeNixTools,
  ...
}:
pkgs.runCommand "frappe-listing-${frappeNixTools.version}"
  {
    inherit (frappeNixTools) version;
    nativeBuildInputs = [ pkgs.makeWrapper ];
    meta = {
      description = "Registry readiness checks, the registry pull request and the README blocks of a Frappe app";
      mainProgram = "frappe-listing";
    };
  }
  ''
    makeWrapper ${lib.getExe frappeNixTools} "$out/bin/frappe-listing" \
      --add-flags listing \
      --suffix PATH : ${
        lib.makeBinPath [
          pkgs.git
          pkgs.gh
          pkgs.uv
        ]
      }
  ''
