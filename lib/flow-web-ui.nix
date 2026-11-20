# The web UI of Frappe's flow harness (`frappe-flow`), built the way Nix builds
# anything: offline, from the lock.
#
# Frappe carries the harness (from 16.60, and on develop) at `flow/harness`, installed
# from there as an editable path source (`frappe-flow = { path =
# "flow/harness", editable = true }`). Its hatch build hook (hatch_build.py)
# packages the browser UI into the wheel, and for anything but an editable
# build it does so by running `npm ci` and `npm run build -w app` in `ui/`.
# The sandbox has neither a registry nor, before this, npm at all, so the
# production environment of every bench on that line failed on this one
# package:
#
#   RuntimeError: npm is required to package the web UI
#
# The hook has a way around itself: a tree with a `PKG-INFO` is a source
# distribution, and the hook then only checks that the UI it should carry is
# there. So the UI is built here, on its own, and lib/python.nix lays it into
# the source tree with a PKG-INFO beside it (hatchling reads it as the sdist's
# core metadata, so it names the package and its version), which is the tree
# the hook would have been handed as an sdist.
#
# The UI is an npm workspace (`ui/package.json` lists `core` and `app`; `app`'s
# vite build writes to `../../src/flow_harness/server/webui`, resolved from
# `ui/app`, which lands in the harness root). It installs with nixpkgs'
# importNpmLock through python-devops-nix's lib.node, the one rule set both
# products follow (frozen, offline, --ignore-scripts), and builds with the same
# `npm run build -w app` the hook runs, so the harness stays the authority on
# how its UI is built.
#
# A harness without the files this relies on (`ui/package.json`,
# `ui/package-lock.json`, `hatch_build.py`, `src/flow_harness`) is left alone and
# null comes back. One that has them but is not built the way this assumes (a
# lock importNpmLock cannot read, a different manager) fails, naming itself,
# where it is built: that is a change to this file, not something to skip.
{
  pkgs,
  lib,
  # python-devops-nix's lib.node.
  node,
  nodejs,
  label ? "frappe-nix",
}:

# The harness root (the directory holding hatch_build.py): a path or a store path.
harness0:

let
  # As a path value. In app mode the harness is a subdirectory of the frappe
  # input, a string that still refers to its store path (it carries the
  # input's context), and so does every string made from it: lib.node uses
  # the lock's file name (`baseNameOf`) as an attribute name, which such a
  # string cannot be, and evaluation stopped there with "the string
  # 'package-lock.json' is not allowed to refer to a store path". The input
  # is fetched before anything here is evaluated, so its store path exists,
  # and as a path it is copied (filtered, below) like any other source.
  harness =
    if builtins.isPath harness0 then
      harness0
    else
      /. + builtins.unsafeDiscardStringContext (toString harness0);

  ui = harness + "/ui";

  recognised =
    builtins.pathExists (ui + "/package.json")
    && builtins.pathExists (ui + "/package-lock.json")
    && builtins.pathExists (harness + "/hatch_build.py")
    && builtins.pathExists (harness + "/src/flow_harness");

  target = node.detect {
    src = ui;
    inherit label;
    key = "frappe-flow/ui";
  };

  # From the manifests alone: an edit to the UI's code does not reinstall.
  nodeModules = node.modules {
    inherit
      pkgs
      label
      target
      nodejs
      ;
    name = "frappe-flow-ui";
    # npm links each workspace member into node_modules (`flow-web -> ../app`),
    # and the store holds only node_modules, so those links dangle there.
    # linkModules below points them at the members in the build tree instead.
    overrides.dontCheckForBrokenSymlinks = true;
  };

  # The UI's own sources. Not its node_modules, if a developer has installed
  # them in the checkout.
  uiSrc = lib.cleanSourceWith {
    src = ui;
    filter = path: type: baseNameOf path != "node_modules" && lib.cleanSourceFilter path type;
    name = "frappe-flow-ui-source";
  };
in
if !recognised then
  null
else
  pkgs.stdenv.mkDerivation {
    name = "frappe-flow-webui";

    dontUnpack = true;
    dontConfigure = true;
    dontFixup = true;

    nativeBuildInputs = [ nodejs ];
    env = node.sandboxEnv;

    buildPhase = ''
      runHook preBuild
      export HOME="$NIX_BUILD_TOP/home"
      mkdir -p "$HOME"

      # vite's outDir is `ui/app`'s `../../src/flow_harness/server/webui`, which
      # lands in this copy's harness root.
      mkdir -p harness
      cp -r --no-preserve=mode ${uiSrc} harness/ui

      (
        cd harness/ui
        modules=${nodeModules}
        ${node.linkModules target.members}
        npm run build -w app
      )
      runHook postBuild
    '';

    installPhase = ''
      runHook preInstall
      webui=harness/src/flow_harness/server/webui
      if [ ! -f "$webui/index.html" ]; then
        echo "frappe-flow's web UI build finished without producing $webui/index.html" >&2
        exit 1
      fi
      cp -r "$webui" "$out"
      runHook postInstall
    '';

    passthru = { inherit nodeModules target; };
  }
