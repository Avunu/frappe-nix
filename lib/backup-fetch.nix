# frappe-nix-backup-fetch: the object-store half of `bench restore`.
#
# python-devops-nix's backup fetch layer (lib.backups.fetch; PLAN.md, Phase 2)
# with frappe-nix's names and its backup layout: one folder per backup, named
# for its timestamp, as Frappe's S3 backup integration writes them
# (py/frappe_nix_tools/frappe_nix_tools/restore/backup_layout.py).
#
#   frappe-nix-backup-fetch list|folders|fetch [--at …] [--files] [--private-files]
#
# `fetch` prints a JSON manifest and touches no database, which is what lets a
# production host reuse it with its own restore half.
#
# The overrides are FRAPPE_NIX_BACKUP_SOURCE, FRAPPE_NIX_MC and
# FRAPPE_NIX_BACKUP_CACHE (CONVENTIONS.md, Tool-owned variables). The names
# frappe-nix used before are read too, with a warning, until the next minor
# release.
{ pkgs, python-devops-nix }:

python-devops-nix.lib.backups.fetch {
  inherit pkgs;
  label = "frappe-nix";
  envPrefix = "FRAPPE";
  layout = ../py/frappe_nix_tools/frappe_nix_tools/restore/backup_layout.py;
  legacyEnv = {
    BACKUP_SOURCE = "FRAPPE_BACKUP_SOURCE";
    MC = "FRAPPE_BACKUP_MC";
    BACKUP_CACHE = "FRAPPE_BACKUP_CACHE";
  };
  # frappe-nix's tool interpreter (PLAN.md, Python versions), as
  # frappe-nix-tools uses.
  python = pkgs.python314;
}
