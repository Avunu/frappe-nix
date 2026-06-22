# Composable Python package overrides for packages that need system libraries.
#
# Pure-Python build deps (setuptools, poetry-core, etc.) should be declared in
# pyproject.toml [tool.uv.extra-build-dependencies] so uv2nix handles them
# automatically. These overlays are only for packages needing native C headers
# or system libraries that can't be expressed in pyproject.toml.
#
# All overrides accept `pkgs` as the first argument (for system packages)
# and return a Python package set overlay (final: prev: { ... }).
#
# Usage in consuming flake:
#   pythonOverrides = lib.composeManyExtensions [
#     (frappe-nix.lib.overrides.mysqlclient { inherit pkgs; mariadb = pkgs.mariadb; })
#     (frappe-nix.lib.overrides.pycups { inherit pkgs; })
#   ];

{
  # mysqlclient needs MariaDB/MySQL headers and client libraries.
  # `connector` provides mysql_config/headers — defaults to mariadb-connector-c
  # (lighter than the full server package). Override with `mariadb` if needed.
  mysqlclient =
    { pkgs, mariadb, connector ? pkgs.mariadb-connector-c }:
    final: prev: {
      mysqlclient = prev.mysqlclient.overrideAttrs (old: {
        nativeBuildInputs = (old.nativeBuildInputs or [ ]) ++ [
          final.setuptools
          pkgs.pkg-config
          connector
        ];
        buildInputs = (old.buildInputs or [ ]) ++ [
          mariadb.client
          pkgs.openssl
          pkgs.zlib
        ];
      });
    };

  # pycups needs CUPS headers and libraries (for printing support).
  pycups =
    { pkgs }:
    final: prev: {
      pycups = prev.pycups.overrideAttrs (old: {
        nativeBuildInputs = (old.nativeBuildInputs or [ ]) ++ [
          final.setuptools
          pkgs.pkg-config
          pkgs.cups.dev
        ];
        buildInputs = (old.buildInputs or [ ]) ++ [
          pkgs.cups
        ];
      });
    };

  # python-ldap needs OpenLDAP headers.
  python-ldap =
    { pkgs }:
    final: prev: {
      python-ldap = prev.python-ldap.overrideAttrs (old: {
        nativeBuildInputs = (old.nativeBuildInputs or [ ]) ++ [
          final.setuptools
          pkgs.pkg-config
        ];
        buildInputs = (old.buildInputs or [ ]) ++ [
          pkgs.openldap
          pkgs.cyrus_sasl
          pkgs.openssl
        ];
      });
    };

  # cairocffi needs cairo headers. Only needed if not using
  # [tool.uv.extra-build-dependencies] for the pure-Python deps.
  cairocffi =
    _:
    final: prev: {
      cairocffi = prev.cairocffi.overrideAttrs (old: {
        nativeBuildInputs = (old.nativeBuildInputs or [ ]) ++ [
          final.setuptools
          final.cffi
          final.pycparser
        ];
      });
    };
}
