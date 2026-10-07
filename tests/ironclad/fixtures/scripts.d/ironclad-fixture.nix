# A stand-in lib/scripts.d file: app mode only, and it uses a snippet
# lib/scripts.nix hands every drop-in.
{
  lib,
  appMode,
  atBench,
  ...
}:
lib.optionalAttrs appMode {
  ironclad-fixture-script = {
    exec = ''
      ${atBench}
      echo "ironclad fixture script"
    '';
    description = "A test script the scripts.d loader must pick up.";
  };
}
