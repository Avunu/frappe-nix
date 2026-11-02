# frappe-nix's own Python package overrides: mysqlclient, which only a Frappe
# bench builds, and semgrep, which only a Frappe bench's dev group carries.
#
# The fixes both frappe-nix and odoo-nix need (psycopg2, python-ldap, lxml,
# pycups, python-magic, pyvips, cairocffi) are python-devops-nix's
# lib.python.overrides, which lib/python.nix applies to every bench right after
# uv2nix's overlay. These, and a bench's own `pythonOverrides`, layer on top.
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

  # semgrep's Linux wheel ships semgrep-core already through patchelf upstream,
  # and patchelf 0.19 cannot grow such a file twice: auto-patchelf sets the
  # interpreter, then the RPATH, and the second aborts in shiftFile (`Assertion
  # splitIndex == -1`), failing the dev shell from semgrep 1.183.0 on. The wheel
  # bundles everything semgrep-core needs but libc in bin/libs/, each library
  # with an `$ORIGIN` RUNPATH, so auto-patchelf is not needed: one call sets the
  # interpreter and a RUNPATH of just `$ORIGIN/libs`. That is no longer than the
  # wheel's own (which also carries a leaked build path), so it is written in
  # place and the file grows once.
  semgrep =
    { pkgs }:
    _final: prev:
    pkgs.lib.optionalAttrs (prev ? semgrep && pkgs.stdenv.hostPlatform.isLinux) {
      semgrep = prev.semgrep.overrideAttrs (old: {
        dontAutoPatchelf = true;
        postFixup = (old.postFixup or "") + ''
          core=$(echo "$out"/lib/python*/site-packages/semgrep/bin/semgrep-core)
          patchelf --set-interpreter "$(< "$NIX_CC/nix-support/dynamic-linker")" \
            --set-rpath '$ORIGIN/libs' "$core"
          "$core" -version
        '';
      });
    };

  pycups = removed "pycups";
  python-ldap = removed "python-ldap";
  pyvips = removed "pyvips";
  cairocffi = removed "cairocffi";
}
