# frappe-nix-backup-fetch: the object-store half of `bench restore`.
#
# python-devops-nix's backup fetch layer (lib.backups.fetch)
# with frappe-nix's names and its backup layout: one folder per backup, named
# for its timestamp, as Frappe's S3 backup integration writes them
# (py/frappe_nix_tools/frappe_nix_tools/restore/backup_layout.py).
#
#   frappe-nix-backup-fetch list|folders|fetch [--at …] [--files] [--private-files]
#
# `fetch` prints a JSON manifest and touches no database, which is what lets a
# production host reuse it with its own restore half.
#
# The overrides are FRAPPE_NIX_BACKUP_SOURCE, FRAPPE_NIX_RCLONE and FRAPPE_NIX_BACKUP_CACHE
# (python-devops-nix docs/conventions.md, Tool-owned variables). The names frappe-nix used
# before (FRAPPE_BACKUP_SOURCE, FRAPPE_BACKUP_CACHE) were read with a warning through 1.x
# and are not read since 2.0.0. FRAPPE_BACKUP_MC named the minio-client binary: the base
# reads the store with rclone now, and refuses it (and FRAPPE_NIX_MC) with what replaced
# them.
{ pkgs, python-devops-nix }:

python-devops-nix.lib.backups.fetch {
  inherit pkgs;
  label = "frappe-nix";
  envPrefix = "FRAPPE";
  layout = ../py/frappe_nix_tools/frappe_nix_tools/restore/backup_layout.py;
  # Refused by the base, naming FRAPPE_NIX_RCLONE (minio-client is gone).
  legacyEnv.MC = "FRAPPE_BACKUP_MC";
  # frappe-nix's tool interpreter (python-devops-nix docs/library.md, Python
  # versions), as frappe-nix-tools uses.
  python = pkgs.python314;
}
