# The packages lib/python.nix grafts into the virtualenvs, built from this
# repository's trees and python-devops-nix's shared Python (PLAN.md, Phase 3;
# CONVENTIONS.md, "The core and the product's package" and "Grafts"):
#
#   devguard  frappe_devguard: the devguard core (python/devguard) merged with
#             lib/devguard/frappe_devguard (_product.py, _frappe.py and the
#             Frappe guards) by lib.devguard.mkPackage, with `settings` baked
#             in as _baked.json. DEVELOPMENT ONLY.
#   unixsock, journald, nodebuild, benchcli
#             lib/<x>/frappe_<x> with the shared _hook.py, by lib.python.mkGraft.
#
# Neither tree may carry a file the other supplies: the build fails naming it.
{ pkgs, python-devops-nix }:

let
  inherit (pkgs) lib;
  base = python-devops-nix.lib;

  graft =
    dir:
    base.python.mkGraft {
      inherit pkgs;
      name = "frappe_${dir}";
      src = ./. + "/${dir}/frappe_${dir}";
    };

  # The core's files, package-relative (python/devguard).
  coreFiles = [
    "__init__.py"
    "_hook.py"
    "_patch.py"
    "_settings.py"
    "guards/__init__.py"
    "guards/egress.py"
    "guards/mail_stdlib.py"
  ];

  graftDirs = [
    "unixsock"
    "journald"
    "nodebuild"
    "benchcli"
  ];
in
rec {
  # `settings`: the baked layer, { enabled, guards.<guard>.<key> } in the
  # schema python/devguard/_settings.py documents.
  devguard =
    settings:
    base.devguard.mkPackage {
      inherit pkgs settings;
      name = "frappe_devguard";
      src = ./devguard/frappe_devguard;
    };

  unixsock = graft "unixsock";
  journald = graft "journald";
  nodebuild = graft "nodebuild";
  benchcli = graft "benchcli";

  # The files a build merges in from python-devops-nix, by the repository path
  # they take in the merged package: { "lib/devguard/frappe_devguard/_hook.py"
  # = <file>; … }. The ty check and frappe-nix's own dev shell copy them into
  # the working tree, so the type checker and the interpreter see the package
  # a build ships. .gitignore keeps them out of commits.
  vendored =
    lib.listToAttrs (
      map (
        file: lib.nameValuePair "lib/devguard/frappe_devguard/${file}" (base.devguard.core + "/${file}")
      ) coreFiles
    )
    // lib.listToAttrs (
      map (dir: lib.nameValuePair "lib/${dir}/frappe_${dir}/_hook.py" base.python.hook) graftDirs
    );

  # Shell lines that copy `vendored` into the tree at `root` (a shell word).
  copyVendored =
    root:
    lib.concatStrings (
      lib.mapAttrsToList (dest: src: ''
        install -Dm644 ${src} ${root}/${dest}
      '') vendored
    );
}
