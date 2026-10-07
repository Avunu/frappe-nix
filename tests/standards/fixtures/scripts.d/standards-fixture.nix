# A stand-in lib/scripts.d file: app mode only, and it uses a snippet
# lib/scripts.nix hands every drop-in. `standards-fixture-args` lists every
# argument it was given, so tests/standards/hookpoints.nix can hold the loader to
# the signature docs/app-standards/spec.md §1.2 promises.
{
  lib,
  appMode,
  atBench,
  ...
}@args:
lib.optionalAttrs appMode {
  standards-fixture-script = {
    exec = ''
      ${atBench}
      echo "standards fixture script"
    '';
    description = "A test script the scripts.d loader must pick up.";
  };
  standards-fixture-args = {
    exec = lib.concatStringsSep " " (builtins.attrNames args);
    description = "The arguments a drop-in receives.";
  };
}
