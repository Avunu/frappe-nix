# `frappe-shots` (docs/app-standards/spec.md §5.5): repeatable screenshots of an app's demo
# site, lib/sh/frappe-shots.sh around the lib/shots engine. Everything that decides a pixel
# comes from frappe-nix's own nixpkgs: chromium, libwebp's cwebp and dwebp, ffmpeg (videos),
# libfaketime, Node 24, and a fixed FONTCONFIG_FILE with the Inter, IBM Plex and Noto sets;
# pixelmatch and pngjs come from lib/shots/yarn.lock, as a Nix-built node_modules beside the
# engine. A dev-shell command of an opted-in app only (lib/standards/shell.nix), and Linux
# only: chromium's and libfaketime's builds are.
{
  pkgs,
  lib,
  frappeNixTools,
  ...
}:
let
  frappeNix = import ./frappe-nix.nix { inherit pkgs lib frappeNixTools; };
  frappeDemo = import ./frappe-demo.nix { inherit pkgs lib frappeNixTools; };

  # The engine: runner.ts, cdp.ts, diff.ts and shots.d.ts, with node_modules built offline
  # from yarn.lock. Node 24 strips the types, so nothing is compiled.
  engine = pkgs.stdenv.mkDerivation {
    pname = "frappe-nix-shots";
    inherit (lib.importJSON ../../shots/package.json) version;
    src = lib.fileset.toSource {
      root = ../../shots;
      fileset = ../../shots;
    };
    yarnOfflineCache = pkgs.fetchYarnDeps {
      yarnLock = ../../shots/yarn.lock;
      hash = "sha256-3AppBlWxTtpLwOV7sA//2nEC9fF3wXlapgdpynlzybg=";
    };
    nativeBuildInputs = [
      pkgs.yarnConfigHook
      pkgs.nodejs_24
    ];
    dontBuild = true;
    installPhase = ''
      runHook preInstall
      mkdir -p "$out"
      cp -r runner.ts cdp.ts diff.ts shots.d.ts package.json node_modules "$out/"
      runHook postInstall
    '';
  };

  # libfaketime for every Nix-built process the bench starts, whichever glibc it has.
  # LD_PRELOAD reaches them all, and they do not share one glibc: devenv's own tools come
  # from devenv's nixpkgs, the bench from frappe-nix's. As built, libfaketime needs its own
  # glibc's libdl, librt and libpthread, which fail in a process on another glibc (a
  # GLIBC_PRIVATE symbol). Since glibc 2.34 those are empty stubs, so this copy drops them
  # and its runpath, and takes libc and libm from the process it is loaded into.
  #
  # Host binaries (a `#!/usr/bin/env` shebang on a CI runner) are older glibcs still, so
  # the preload names `<dir>/$LIB/libfaketime.so.1`: Nix's ld.so expands $LIB to `lib` and
  # finds it, a multiarch host's to `lib/x86_64-linux-gnu`, where there is nothing, and it
  # only warns that the object cannot be preloaded.
  faketime = pkgs.runCommand "frappe-shots-faketime" { nativeBuildInputs = [ pkgs.patchelf ]; } ''
    mkdir -p "$out/lib"
    cp ${pkgs.libfaketime}/lib/libfaketime.so.1 "$out/lib/libfaketime.so.1"
    chmod u+w "$out/lib/libfaketime.so.1"
    patchelf \
      --remove-needed libdl.so.2 \
      --remove-needed librt.so.1 \
      --remove-needed libpthread.so.0 \
      --remove-rpath \
      "$out/lib/libfaketime.so.1"
  '';

  fonts = pkgs.makeFontsConf {
    fontDirectories = [
      pkgs.inter
      pkgs.ibm-plex
      pkgs.noto-fonts
      pkgs.noto-fonts-color-emoji
    ];
  };

  linux = pkgs.writeShellApplication {
    name = "frappe-shots";
    runtimeInputs = [
      pkgs.coreutils
      pkgs.curl
      pkgs.git
      pkgs.jq
      pkgs.nodejs_24
      pkgs.chromium
      pkgs.libwebp
      pkgs.ffmpeg-headless
      frappeNix
      frappeDemo
    ];
    runtimeEnv.FONTCONFIG_FILE = "${fonts}";
    text =
      builtins.replaceStrings
        [ "@SHOTS_DIR@" "@FAKETIME_LIB@" ]
        [ "${engine}" "${faketime}/$LIB/libfaketime.so.1" ]
        (builtins.readFile ../../sh/frappe-shots.sh);
    passthru = { inherit engine; };
    meta.description = "Take repeatable screenshots of a Frappe app's demo site and compare them with the committed ones";
  };

  elsewhere = pkgs.writeShellApplication {
    name = "frappe-shots";
    text = ''
      echo "frappe-shots runs on Linux (chromium and libfaketime from frappe-nix's nixpkgs)" >&2
      exit 3
    '';
    meta.description = "Take repeatable screenshots of a Frappe app's demo site (Linux only)";
  };
in
if pkgs.stdenv.hostPlatform.isLinux then linux else elsewhere
