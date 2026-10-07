# A tests/standards area that would replace frappe-nix's own `ty` check: the
# loader must refuse a check not named standards-<name>.
{ pkgs, ... }:
{
  ty = pkgs.emptyFile;
}
