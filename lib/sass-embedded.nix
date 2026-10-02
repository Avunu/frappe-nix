# Native Sass for frappe's esbuild pipeline: the `sass-embedded` npm package,
# with nixpkgs' Dart Sass as its compiler.
#
# Frappe compiles stylesheets through @frappe/esbuild-plugin-postcss2, which
# require()s the `sass` package — Dart Sass compiled to JavaScript — and calls
# its legacy render(). Measured on Carbon-based stylesheets, that path takes
# 4–10 s per stylesheet; the same stylesheet through sass-embedded and the
# native compiler takes 1–1.6 s, with the same JS API: legacy render(), JS
# importer functions (frappe's sass_options strips "~" with one) and
# stats.includedFiles (what tells esbuild's watcher to rebuild on a partial).
# lib/js/esbuild-preload.js hands this to frappe's require("sass").
#
# Built from the committed lock (lib/sass-embedded/package-lock.json), every
# package fetched by its recorded integrity hash, except the optional ones:
# the 18 prebuilt `sass-embedded-<platform>` compilers, which are dynamically
# linked against an FHS loader and would not run here, and @parcel/watcher,
# which only the sass CLI's --watch uses. In their place is the one directory
# sass-embedded looks for (dist/lib/src/compiler-path.js tries
# `sass-embedded-<platform>-<arch>/dart-sass/sass`), holding pkgs.dart-sass.
#
# The lock pins sass-embedded to the version nixpkgs packaged dart-sass at when
# it was written; the embedded protocol is stable across 1.x, so a later
# compiler from a nixpkgs bump keeps working. Regenerate with
# `npm install --package-lock-only` in lib/sass-embedded/ to move the pin.
{ pkgs }:

let
  inherit (pkgs) lib;

  lock = lib.importJSON ./sass-embedded/package-lock.json;

  required = lib.filterAttrs (path: p: path != "" && !(p.optional or false)) lock.packages;

  platform = pkgs.stdenv.hostPlatform;
  compilerModule = "sass-embedded-${if platform.isDarwin then "darwin" else "linux"}-${
    if platform.isAarch64 then "arm64" else "x64"
  }";
in
pkgs.runCommand "frappe-nix-sass-embedded-${required."node_modules/sass-embedded".version}"
  {
    passthru.module = "node_modules/sass-embedded";
  }
  ''
    ${lib.concatStrings (
      lib.mapAttrsToList (path: p: ''
        mkdir -p "$out/${path}"
        tar -xzf ${
          pkgs.fetchurl {
            url = p.resolved;
            hash = p.integrity;
          }
        } -C "$out/${path}" --strip-components=1
      '') required
    )}

    mkdir -p "$out/node_modules/${compilerModule}/dart-sass"
    ln -s ${lib.getExe' pkgs.dart-sass "sass"} "$out/node_modules/${compilerModule}/dart-sass/sass"
  ''
