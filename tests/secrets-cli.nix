# edit-secret / rekey-secrets driven against a real ragenix and real keys.
#
# The scripts are rendered from lib/scripts.nix twice, against two recipient
# lists — alice, then alice+bob — so "someone changed `recipients` in flake.nix
# and ran rekey-secrets" is an actual sequence here rather than an assumption.
{ pkgs }:

let
  inherit (pkgs) lib;
  schema = import ../lib/secrets-schema.nix { inherit lib; };

  mkCfg = recipients: rec {
    enable = true;
    dir = ./fixtures/secrets;
    relDir = "secrets";
    inherit recipients;
    hostRecipients = { };
    backupAccess = {
      enable = true;
      hosts = false;
    };
    sites = { };
    extra = { };
    identityPaths = [ "$HOME/.ssh/id_ed25519" ];
    declared = schema.secretList (mkCfg recipients);
  };

  scriptsFor =
    recipients:
    import ../lib/scripts.nix {
      inherit lib pkgs;
      appsWithNode = [ ];
      benchBin = "bench";
      secrets = import ../lib/secrets-tools.nix {
        inherit lib pkgs schema;
        cfg = mkCfg recipients;
      };
    };

  # Fixture keypairs, committed. They have to be known at eval time: the
  # recipient list ends up in a store file the rendered script reads by path,
  # so keys generated in the sandbox could not reach it.
  pub = name: lib.head (lib.splitString "\n" (builtins.readFile ./fixtures/keys/${name}.pub));

  one = scriptsFor { alice = pub "alice"; };
  two = scriptsFor {
    alice = pub "alice";
    bob = pub "bob";
  };
in
{
  secrets-cli = pkgs.runCommand "frappe-nix-secrets-cli-check" {
    nativeBuildInputs = with pkgs; [
      ragenix
      rage
      openssh
      git
      jq
      python3
      coreutils
    ];
    editSecret = one.edit-secret.exec;
    checkSecret = one.check-secrets.exec;
    rekeySecrets = two.rekey-secrets.exec;
    passAsFile = [ "editSecret" "checkSecret" "rekeySecrets" ];
  } ''
    export HOME="$PWD"
    install -m 0600 ${./fixtures/keys/alice} alice
    install -m 0600 ${./fixtures/keys/bob} bob
    cp ${./fixtures/keys/alice.pub} alice.pub
    cp ${./fixtures/keys/bob.pub} bob.pub

    bash ${./secrets-cli.sh} \
      "$editSecretPath" "$rekeySecretsPath" "$checkSecretPath" \
      "$PWD" | tee "$out"
  '';
}
