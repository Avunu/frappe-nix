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
        [ "${engine}" "${pkgs.libfaketime}/lib/libfaketime.so.1" ]
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
