# frappe-nix's own Python package override: mysqlclient, which only a Frappe
# bench builds.
#
# The fixes both frappe-nix and odoo-nix need (psycopg2, python-ldap, lxml,
# pycups, python-magic, pyvips, cairocffi) are python-devops-nix's
# lib.python.overrides, which lib/python.nix applies to every bench right after
# uv2nix's overlay. This one, and a bench's own `pythonOverrides`, layer on top.
#
# Pure-Python build dependencies (setuptools, poetry-core and the like) are not
# overlays: they belong in [tool.uv.extra-build-dependencies], which
# `frappe-nix-build-deps` derives from uv.lock (lib/build-deps.nix).
#
# Each override takes `pkgs` (for system packages) and returns a Python package
# set overlay (final: prev: { ... }).
let
  # pycups, python-ldap, pyvips and cairocffi were frappe-nix's to apply; the
  # base's set now applies them to every bench. 1.x kept their names as no-ops
  # that warned; since 2.0.0 a bench that still lists one in its
  # pythonOverrides is refused, naming the fix.
  removed =
    name: _:
    throw "frappe-nix: lib.overrides.${name} was removed in 2.0.0. python-devops-nix's lib.python.overrides applies it to every bench already; remove it from pythonOverrides.";
in
{
  # mysqlclient needs MariaDB/MySQL headers and client libraries.
  mysqlclient =
    { pkgs, mariadb }:
    final: prev: {
      mysqlclient = prev.mysqlclient.overrideAttrs (old: {
        nativeBuildInputs = (old.nativeBuildInputs or [ ]) ++ [
          final.setuptools
          pkgs.pkg-config
          # mariadb_config + client headers (mysql.h). pkgs.mariadb has no `dev`
          # output, so use the connector-c package for the build-time headers.
          pkgs.mariadb-connector-c
        ];
        buildInputs = (old.buildInputs or [ ]) ++ [
          mariadb.client
          pkgs.openssl
          pkgs.zlib
        ];
      });
    };

  pycups = removed "pycups";
  python-ldap = removed "python-ldap";
  pyvips = removed "pyvips";
  cairocffi = removed "cairocffi";
}
