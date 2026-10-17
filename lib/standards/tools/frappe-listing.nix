# `frappe-listing` = `frappe-nix listing` (docs/app-standards/spec.md §5.2): registry
# readiness (L1 to L12), the registry pull request and the README blocks. A wrapper around
# frappe-nix-tools' bin/frappe-nix, like tools/frappe-nix.nix, with what the rules call
# appended to PATH (so the app's own copies win): git and gh (the registry pull request)
# and uv (L9 builds pilot's validation bench with it). L7's semgrep is the app's own, from
# its tools/ project (the semgrep module pins it there), else `uvx` at the floor version.
#
# L9's ImportCheck can build frappe's mysqlclient from source, which needs MariaDB client
# headers and a library to link. An opted-in app's dev shell has neither (nor does `nix run`),
# so the wrapper points mysqlclient's own MYSQLCLIENT_CFLAGS / MYSQLCLIENT_LDFLAGS at
# nixpkgs' mariadb-connector-c, with an rpath so the built extension loads without
# LD_LIBRARY_PATH. They are set only in frappe-listing's environment, and only as defaults:
# a value the caller exports wins, and no shell sees them. A C compiler is still the shell's
# (the dev shell has gcc).
{
  pkgs,
  lib,
  frappeNixTools,
  ...
}:
let
  mariadb = pkgs.mariadb-connector-c;
  mysqlclientCflags = "-I${lib.getDev mariadb}/include/mariadb";
  mysqlclientLdflags = "-L${lib.getLib mariadb}/lib/mariadb -Wl,-rpath,${lib.getLib mariadb}/lib/mariadb -lmariadb";
in
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
      --set-default MYSQLCLIENT_CFLAGS ${lib.escapeShellArg mysqlclientCflags} \
      --set-default MYSQLCLIENT_LDFLAGS ${lib.escapeShellArg mysqlclientLdflags} \
      --suffix PATH : ${
        lib.makeBinPath [
          pkgs.git
          pkgs.gh
          pkgs.uv
        ]
      }
  ''
