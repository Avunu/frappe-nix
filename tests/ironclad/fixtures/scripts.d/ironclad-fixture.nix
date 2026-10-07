# A stand-in lib/scripts.d file: app mode only, and it uses a snippet
# lib/scripts.nix hands every drop-in. `ironclad-fixture-args` lists every
# argument it was given, so tests/ironclad/hookpoints.nix can hold the loader to
# the signature docs/ironclad/spec.md §1.4 promises.
{
  lib,
  appMode,
  atBench,
  ...
}@args:
lib.optionalAttrs appMode {
  ironclad-fixture-script = {
    exec = ''
      ${atBench}
      echo "ironclad fixture script"
    '';
    description = "A test script the scripts.d loader must pick up.";
  };
  ironclad-fixture-args = {
    exec = lib.concatStringsSep " " (builtins.attrNames args);
    description = "The arguments a drop-in receives.";
  };
}
