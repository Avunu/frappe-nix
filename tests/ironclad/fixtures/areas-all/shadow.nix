# A tests/ironclad area that would replace `ironclad-all`: the loader must
# refuse it.
{ pkgs, ... }:
{
  ironclad-all = pkgs.emptyFile;
}
