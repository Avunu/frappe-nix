# A tests/ironclad area that would replace frappe-nix's own `ty` check: the
# loader must refuse a check not named ironclad-<name>.
{ pkgs, ... }:
{
  ty = pkgs.emptyFile;
}
