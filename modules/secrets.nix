# Secrets for a frappe-nix bench: agenix for the ciphertext, agenix-shell for
# the dev shell, one declaration serving both, and the deployment host too.
#
# The option tree, the schema and the agenix-shell wiring are the shared
# secrets toolkit's (python-devops-nix, lib.secrets.mkModule; PLAN.md, Phase 2).
# What is frappe-nix's own is below: the option path, the `frappe_` variable
# prefix, and `sites`, a group whose units are Frappe sites, each with the three
# secrets services.frappe-nix consumes in production.
#
# TOP-LEVEL, not perSystem, deliberately. agenix-shell.secrets is a top-level
# option and there is no supported way to reach a perSystem value from there;
# and recipients, .age paths and audiences are facts about the bench, not about
# x86_64-linux. The rule of thumb: top level says *who* and *what*, perSystem
# (modules/devenv.nix) says *how the shell behaves*.
{ inputs, ... }:
{
  imports = [
    (inputs.python-devops-nix.lib.secrets.mkModule {
      optionPath = "frappe-nix.secrets";
      label = "frappe-nix";
      varPrefix = "frappe";
      # The module imports agenix-shell's flake module itself, so a bench
      # declares only `frappe-nix.secrets` and never agenix-shell directly.
      # flake-parts deduplicates it by key, which importApply sets to the store
      # path of agenix-shell's own module file, and lib.mkFlake's
      # `self.inputs // consumerInputs` merge makes a consumer's copy resolve to
      # the same path.
      inherit (inputs) agenix-shell;
      # flake.lib.secrets, the name both products share; frappeSecrets for the
      # deprecation window (CONVENTIONS.md, Secrets).
      libAliases = [ "frappeSecrets" ];

      groups.sites = {
        description = ''
          Per-site secrets. `sites."erp.example.com" = { };` declares all three:

              <dir>/erp.example.com/encryption-key.age   (one line)
              <dir>/erp.example.com/db-password.age      (one line)
              <dir>/erp.example.com/site-config.age      (JSON object)

          The same ciphertext feeds the dev shell and, through
          `flake.lib.secrets`, `services.frappe-nix`'s `encryptionKeyFile` /
          `database.passwordFile` / `extraConfigFiles`. One declaration, one
          file, both ends.
        '';
        example = {
          "erp.example.com" = { };
        };
        developersDescription = ''
          Let `recipients` read this site's secrets, not just `hostRecipients`.

          On by default: reading the production encryption key is what keeps
          encrypted Password and API-secret fields legible after a dev restore.
          Turning it off gives a useful capability tier: a contractor who can
          fetch and restore the database but cannot read the credentials stored
          inside it.
        '';
        # The shapes mirror what services.frappe-nix consumes in production
        # (encryptionKeyFile, database.passwordFile, extraConfigFiles), and the
        # deployment host reads all three, so host keys always apply.
        roles = [
          {
            attr = "encryptionKey";
            file = "encryption-key";
            format = "raw";
            var = "encryption_key";
            description = ''
              Declare `<dir>/<site>/encryption-key.age`: the Frappe encryption
              key, one line. This is the key that decrypts every stored Password
              and API-secret field, so it is also what `services.frappe-nix`'s
              `encryptionKeyFile` wants in production.
            '';
          }
          {
            attr = "databasePassword";
            file = "db-password";
            format = "raw";
            var = "db_password";
            description = ''
              Declare `<dir>/<site>/db-password.age`, one line: the production
              counterpart of `services.frappe-nix.sites.<name>.database.passwordFile`.
            '';
          }
          {
            attr = "extraConfig";
            file = "site-config";
            format = "json";
            var = "site_config";
            description = ''
              Declare `<dir>/<site>/site-config.age`, a JSON object deep-merged
              into `site_config.json`: the counterpart of
              `services.frappe-nix.sites.<name>.extraConfigFiles`. Object-storage and
              third-party credentials belong here.
            '';
          }
        ];
      };
    })
  ];
}
