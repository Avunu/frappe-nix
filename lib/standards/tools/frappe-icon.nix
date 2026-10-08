# `frappe-icon` = `frappe-nix icon` (docs/app-standards/spec.md §5.3): the app's symbolic
# icon and logo tile, their structural and raster checks, the PNG outputs and the
# desktop-icon fixture. resvg comes from frappe-nix's own nixpkgs, so the raster checks give
# the same pixels in the dev shell, in selftest-product and in the nightly `links` job.
{
  pkgs,
  lib,
  frappeNixTools,
  ...
}:
pkgs.runCommand "frappe-icon-${frappeNixTools.version}"
  {
    inherit (frappeNixTools) version;
    nativeBuildInputs = [ pkgs.makeWrapper ];
    meta = {
      description = "Check a Frappe app's icons, make its logo tile and render its favicons";
      mainProgram = "frappe-icon";
    };
  }
  ''
    makeWrapper ${lib.getExe frappeNixTools} "$out/bin/frappe-icon" \
      --add-flags icon \
      --prefix PATH : ${lib.makeBinPath [ pkgs.resvg ]} \
      --suffix PATH : ${lib.makeBinPath [ pkgs.git ]}
  ''
