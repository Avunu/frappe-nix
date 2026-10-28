# frappe-nix.secrets, evaluated the way a bench declares it: frappe-nix's
# flake module in a fixture flake built with frappe-nix's own lib.mkFlake.
#
# The toolkit itself (the schema, agecheck, the scripts, the module's option
# tree) is python-devops-nix's and tested there. What is frappe-nix's is the
# call: the option path, the `frappe_` variable names, `sites` as a group with
# its three roles, host keys on every site secret, and flake.lib.secrets (and
# not frappeSecrets, its 1.x alias). Those are what a bench's .age paths, its
# shell variables and its deployment's age.secrets depend on, so they are
# pinned here.
{ self, pkgs }:
let
  inherit (pkgs) lib;
  checks = self.inputs.python-devops-nix.lib.checks.mk {
    inherit pkgs;
    product = "frappe-nix";
  };

  pub = name: lib.head (lib.splitString "\n" (builtins.readFile ./fixtures/keys/${name}.pub));
  alice = pub "alice";
  bob = pub "bob";
  dir = ./fixtures/secrets;

  flake = self.lib.mkFlake { inputs.self = { }; } {
    imports = [ self.flakeModules.default ];
    debug = true;
    systems = [ ];
    frappe-nix.secrets = {
      inherit dir;
      recipients.alice = alice;
      hostRecipients.app = bob;
      sites."erp.example.com".extraConfig = false;
      sites."b.example.com".developers = false;
      extra.api = {
        format = "json";
        hosts = true;
      };
    };
  };
  inherit (flake.debug) config;
  cfg = config.frappe-nix.secrets;

  # The rules agenix reads, before they are rendered with absolute paths.
  rules = lib.listToAttrs (map (s: lib.nameValuePair s.relPath s.publicKeys) cfg.declared);

  pairs = {
    vars = {
      expected = [
        "frappe_api"
        "frappe_backup_access"
        "frappe_db_password_b_example_com"
        "frappe_db_password_erp_example_com"
        "frappe_encryption_key_b_example_com"
        "frappe_encryption_key_erp_example_com"
        "frappe_site_config_b_example_com"
      ];
      actual = lib.sort lib.lessThan (map (s: s.var) cfg.declared);
    };
    rules = {
      expected = {
        "secrets/api.age" = [
          alice
          bob
        ];
        "secrets/backup-access.age" = [ alice ];
        # developers = false: the host reads it, the humans do not.
        "secrets/b.example.com/db-password.age" = [ bob ];
        "secrets/b.example.com/encryption-key.age" = [ bob ];
        "secrets/b.example.com/site-config.age" = [ bob ];
        "secrets/erp.example.com/db-password.age" = [
          alice
          bob
        ];
        "secrets/erp.example.com/encryption-key.age" = [
          alice
          bob
        ];
      };
      actual = rules;
    };
    # Only the .age files that exist: a declared secret not yet written must
    # not break evaluation (and with it edit-secret, which writes it).
    agenix-shell = {
      expected = {
        frappe_backup_access = {
          name = "frappe_backup_access";
          mode = "0400";
          file = toString (dir + "/backup-access.age");
        };
      };
      actual = lib.mapAttrs (_: s: {
        inherit (s) name mode;
        file = toString s.file;
      }) config.agenix-shell.secrets;
    };
    site-options = {
      expected = {
        encryptionKey = true;
        databasePassword = true;
        extraConfig = false;
        developers = true;
      };
      actual = cfg.sites."erp.example.com";
    };
    lib-output = {
      expected = {
        keys = [
          "files"
          "recipients"
          "sites"
        ];
        sites."erp.example.com" = [
          "databasePassword"
          "encryptionKey"
        ];
        alias = false;
      };
      actual = {
        keys = lib.attrNames flake.lib.secrets;
        sites = lib.mapAttrs (_: lib.attrNames) (
          lib.filterAttrs (n: _: n == "erp.example.com") flake.lib.secrets.sites
        );
        alias = flake.lib ? frappeSecrets;
      };
    };
  };

  built = lib.mapAttrsToList (name: checks.eval "secrets-module-${name}") pairs;
in
{
  secrets-module = checks.script "secrets-module" { } ''
    ${lib.concatMapStrings (drv: ''
      test -e ${drv} && echo "  ok   ${drv.name}" | tee -a "$out"
    '') built}
  '';
}
