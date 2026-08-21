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

  # pyvips uses cffi ffi.dlopen at runtime and needs the absolute store path
  # for libvips — ctypes.util.find_library won't find it in the Nix store.
  pyvips =
    { pkgs }:
    final: prev: {
      pyvips = prev.pyvips.overrideAttrs (old: {
        buildInputs = (old.buildInputs or [ ]) ++ [ pkgs.vips ];
        postPatch = ''
          substituteInPlace pyvips/__init__.py \
            --replace "library_name('vips', 42)" "'${pkgs.lib.getLib pkgs.vips}/lib/libvips.so.42'"
        '';
      });
    };

  # cairocffi is a cffi wrapper, not a compiled extension: it calls
  # ffi.dlopen('libcairo.so.2') at *import* time, by bare filename. Nix has
  # no ldconfig cache and nothing on LD_LIBRARY_PATH by default, so that
  # dlopen fails outright unless libcairo is put on the loader's path somehow.
  #
  # Putting cairo's whole /lib on LD_LIBRARY_PATH is a trap: it also carries
  # cairo's own libexpat, which shadows the interpreter's ABI-incompatible
  # one and breaks Python's built-in XML parsing everywhere, not just cairo.
  # Nix already bakes a RUNPATH into libcairo.so.2 for its own dependencies
  # (libpng, freetype, fontconfig, pixman, X11 libs), so patching the dlopen
  # call to the absolute store path resolves everything through that
  # self-contained RUNPATH with nothing added to LD_LIBRARY_PATH - same fix
  # shape as pyvips above, for the same underlying reason.
  cairocffi =
    { pkgs }:
    final: prev: {
      cairocffi = prev.cairocffi.overrideAttrs (old: {
        nativeBuildInputs = (old.nativeBuildInputs or [ ]) ++ [
          final.setuptools
          final.cffi
          final.pycparser
        ];
        buildInputs = (old.buildInputs or [ ]) ++ [ pkgs.cairo ];
        postPatch = ''
          substituteInPlace cairocffi/__init__.py \
            --replace "'libcairo.so.2'" "'${pkgs.lib.getLib pkgs.cairo}/lib/libcairo.so.2'"
        '';
      });
    };
}
