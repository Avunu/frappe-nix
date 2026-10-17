# `bench restore`, rendered from lib/scripts.nix with secrets and fetching
# switched on, then driven against a fixture bucket and a stub bench.
#
# The script is a shim that execs the Python command (frappe-nix-bench restore,
# py/frappe_nix_tools/frappe_nix_tools/restore/) with the configuration
# lib/scripts.nix generates for it, so this drives the real thing: the shim,
# the generated JSON and the command, against the real backup fetch command.
{ pkgs, python-devops-nix }:

let
  inherit (pkgs) lib;

  schema = python-devops-nix.lib.secrets.schema {
    varPrefix = "frappe";
  };

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

  secrets = import ../lib/secrets.nix {
    inherit pkgs python-devops-nix;
    cfg = secretsCfg;
  };

  fetch = import ../lib/backup-fetch.nix { inherit pkgs python-devops-nix; };

  inherit (import ../lib/standards/outputs.nix { inherit pkgs; }) frappeNixTools;

  render =
    {
      devguard ? true,
      enable ? true,
      files ? "none",
    }:
    (import ../lib/scripts.nix {
      inherit lib pkgs;
      appsWithNode = [ ];
      benchBin = "bench";
      benchCli = import ../lib/bench-cli.nix { inherit pkgs frappeNixTools; };
      inherit secrets;
      restore = {
        inherit devguard enable files;
        prefix = "";
        carryConfigKeys = [
          "encryption_key"
          "backup_encryption_key"
        ];
        migrate = true;
        neutralize = true;
        requireDevguard = true;
        fetch = lib.getExe fetch;
        # The real one decrypts; here the credentials come from
        # FRAPPE_NIX_BACKUP_SOURCE, so this only has to be a no-op that proves
        # the hook is wired.
        loadSecrets = ":";
      };
    }).bench-restore.exec;
in
{
  bench-restore =
    pkgs.runCommand "frappe-nix-bench-restore-check"
      {
        nativeBuildInputs = with pkgs; [
          jq
          coreutils
          shellcheck
        ];
        guarded = render { };
        unguarded = render { devguard = false; };
        nofetch = render { enable = false; };
        allfiles = render { files = "all"; };
        passAsFile = [
          "guarded"
          "unguarded"
          "nofetch"
          "allfiles"
        ];
      }
      ''
        export HOME="$PWD"

        # devenv would ship these unlinted; lint them here instead.
        for f in "$guardedPath" "$unguardedPath" "$nofetchPath" "$allfilesPath"; do
          shellcheck -s bash -S warning -e SC2317 "$f"
        done

        # Each shim is the two lines it should be, and hands the command a
        # configuration that says whether the bench is guarded: generated, so a
        # refactor could silently drop it.
        config() { grep -o '/nix/store/[^ ]*-frappe-nix-cli.json' "$1"; }
        [ "$(jq -r .restore.devguard "$(config "$unguardedPath")")" = false ] ||
          { echo "the unguarded build does not say so" >&2; exit 1; }
        [ "$(jq -r .restore.devguard "$(config "$guardedPath")")" = true ] ||
          { echo "the guarded build does not say so" >&2; exit 1; }
        [ "$(grep -c . "$guardedPath")" -le 2 ] ||
          { echo "bench-restore is more than a shim:" >&2; cat "$guardedPath" >&2; exit 1; }

        UNGUARDED_SCRIPT="$unguardedPath" NOFETCH_SCRIPT="$nofetchPath" \
          ALLFILES_SCRIPT="$allfilesPath" \
          bash ${./bench-restore.sh} "$guardedPath" | tee "$out"
      '';
}
