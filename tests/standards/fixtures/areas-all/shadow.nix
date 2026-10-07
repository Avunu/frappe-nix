# A tests/standards area that would replace `standards-all`: the loader must
# refuse it.
{ pkgs, ... }:
{
  standards-all = pkgs.emptyFile;
}
