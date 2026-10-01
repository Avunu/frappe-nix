# An editable workspace member's source, cut down to what its editable build
# reads. Used by lib/python.nix on the development virtualenv only.
#
# An editable install is a .pth file naming `$FRAPPE_BENCH_ROOT/apps/<app>`
# (from editableRoot, not from src) plus metadata; it carries none of the app's
# code. Built from the whole app directory, though, the derivation is keyed on
# every byte in it: each evaluation after any edit copied the app into the store
# afresh (erpnext alone is ~150 MB) and rebuilt the package and the virtualenv
# around it — for a result that had not changed. uv2nix's own source-filtering
# guide recommends exactly this cut for editable packages.
#
# flit_core only, which is what every Frappe app builds with, because its
# editable build is fully described: pyproject.toml; the readme and license file
# it names; the LICENSE*/COPYING*-style files it copies into dist-info by name
# on its own; and the module's __init__.py (for `__version__`, read by parsing,
# and for the module's existence). Not the rest of the top level: an app's
# package.json, yarn.lock and uv.lock live there too and change far more often
# than its metadata. A member declaring PEP 639 `license-files` globs or flit
# `external-data`, both of which can reach into subdirectories, keeps its full
# source rather than risk a build that fails on a missing file. So does a src
# that is not a path — app mode's store-path inputs, which python.nix's
# srcOverlay re-points after this anyway.
{ lib }:

src:
let
  pyproject = builtins.fromTOML (builtins.readFile (src + "/pyproject.toml"));
  project = pyproject.project or { };
  flit = pyproject.tool.flit or { };
  module = flit.module.name or (builtins.replaceStrings [ "-" "." ] [ "_" "_" ] (project.name or ""));
  # `readme` and `license` may each be a string or a { file = …; } table; a
  # string license is an SPDX expression, not a file.
  readme =
    let
      r = project.readme or null;
    in
    if builtins.isAttrs r then r.file or null else r;
  licenseFile =
    if builtins.isAttrs (project.license or null) then project.license.file or null else null;
  licenseLike =
    name: builtins.match "(LICEN[CS]E|COPYING|NOTICE|AUTHORS).*" (lib.toUpper name) != null;
  namedFiles = lib.mapAttrsToList (name: _: src + "/${name}") (
    lib.filterAttrs (name: type: type == "regular" && licenseLike name) (builtins.readDir src)
  );
  trimmable =
    builtins.isPath src
    && builtins.pathExists (src + "/pyproject.toml")
    && (pyproject.build-system.build-backend or "") == "flit_core.buildapi"
    && !(project ? license-files)
    && !(flit ? external-data)
    && module != "";
in
if !trimmable then
  src
else
  lib.fileset.toSource {
    root = src;
    fileset = lib.fileset.unions (
      [ (src + "/pyproject.toml") ]
      ++ namedFiles
      ++ map lib.fileset.maybeMissing (
        [
          (src + "/${module}/__init__.py")
          (src + "/${module}.py")
          (src + "/src/${module}/__init__.py")
          (src + "/src/${module}.py")
        ]
        ++ map (f: src + "/${f}") (
          lib.filter (f: f != null) [
            readme
            licenseFile
          ]
        )
      )
    );
  }
