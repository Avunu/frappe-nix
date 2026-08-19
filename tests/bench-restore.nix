# The `bench restore` script, rendered from lib/scripts.nix with secrets and
# fetching switched on, then driven against a fixture bucket and a stub bench.
#
# Rendering it here rather than testing lib/scripts.nix's output by inspection
# is the point: devenv never shellchecks a `scripts.<n>.exec` body, so without
# this the largest script in the repo would ship unlinted and unexercised.
{ pkgs }:

let
  inherit (pkgs) lib;

  schema = import ../lib/secrets-schema.nix { inherit lib; };

  secretsCfg = rec {
    enable = true;
    dir = ./fixtures/secrets;
    relDir = "secrets";
    recipients.dev = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOv4SpIhHJqtRaYBRQOin4PTDUxRwo7ozoQHTUFjMGLW";
    hostRecipients = { };
    backupAccess = {
      enable = true;
      hosts = false;
    };
    sites = { };
    extra = { };
    identityPaths = [ "$HOME/.ssh/id_ed25519" ];
    declared = schema.secretList secretsCfg;
  };

  secrets = import ../lib/secrets-tools.nix {
    inherit lib pkgs schema;
    cfg = secretsCfg;
  };

  fetch = import ../lib/backup-fetch.nix { inherit pkgs; };

  render =
    devguard:
    (import ../lib/scripts.nix {
      inherit lib pkgs;
      appsWithNode = [ ];
      benchBin = "bench";
      inherit secrets;
      restore = {
        inherit devguard;
        enable = true;
        prefix = "";
        withFiles = "none";
        carryConfigKeys = [ "encryption_key" "backup_encryption_key" ];
        migrate = true;
        requireDevguard = true;
        fetch = "${fetch}/bin/frappe-nix-backup-fetch";
        # The real one decrypts; here the credentials come from
        # FRAPPE_BACKUP_SOURCE, so this only has to be a no-op that proves the
        # hook is wired.
        loadSecrets = ":";
      };
    }).bench-restore.exec;
in
{
  bench-restore = pkgs.runCommand "frappe-nix-bench-restore-check" {
    nativeBuildInputs = with pkgs; [
      jq
      minio-client
      coreutils
      shellcheck
    ];
    guarded = render true;
    unguarded = render false;
    passAsFile = [ "guarded" "unguarded" ];
  } ''
    export HOME="$PWD"

    # devenv would ship these unlinted; lint them here instead.
    for f in "$guardedPath" "$unguardedPath"; do
      shellcheck -s bash -S warning -e SC2317 "$f"
    done

    # The devguard interlock has to be absent when the guards are on and present
    # when they are off — it is generated, so a refactor could silently drop it.
    grep -q FRAPPE_RESTORE_ALLOW_UNGUARDED "$unguardedPath" ||
      { echo "devguard interlock missing from the unguarded build" >&2; exit 1; }
    ! grep -q FRAPPE_RESTORE_ALLOW_UNGUARDED "$guardedPath" ||
      { echo "devguard interlock present when guards are on" >&2; exit 1; }

    bash ${./bench-restore.sh} "$guardedPath" | tee "$out"
  '';
}
