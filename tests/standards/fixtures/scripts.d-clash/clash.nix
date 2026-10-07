# A drop-in that redefines one of lib/scripts.nix's own scripts: evaluation must fail.
_: {
  bench-update.exec = "true";
}
