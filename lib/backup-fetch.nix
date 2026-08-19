# frappe-nix-backup-fetch — the object-store half of `bench restore`.
#
# A writeShellApplication rather than a devenv `scripts.<n>.exec` string, for
# the same reason lib/init.nix is one: devenv script bodies are never
# shellchecked, and this is a few hundred lines of quoting-sensitive discovery
# logic. Building it separately also makes it callable from the NixOS side,
# which today carries a near-identical copy of the same shell.
{ pkgs }:

pkgs.writeShellApplication {
  name = "frappe-nix-backup-fetch";

  runtimeInputs = with pkgs; [
    minio-client # `mc` — S3 protocol, and it reads local paths identically
    jq
    coreutils
    findutils
    gnugrep
    gnused
  ];

  text = builtins.readFile ./sh/backup-fetch.sh;
}
