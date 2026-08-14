# Secrets for a frappe-nix bench: agenix for the ciphertext, agenix-shell for
# the dev shell, one declaration serving both — and the deployment host too.
#
# TOP-LEVEL, not perSystem, deliberately. agenix-shell.secrets is a top-level
# option and there is no supported way to reach a perSystem value from there;
# and recipients, .age paths and audiences are facts about the bench, not about
# x86_64-linux. The rule of thumb: top level says *who* and *what*, perSystem
# (modules/devenv.nix) says *how the shell behaves*.
{
  config,
  lib,
  ...
}:

let
  inherit (lib) mkOption mkEnableOption types literalExpression;

  cfg = config.frappe-nix.secrets;
  # No `self`: flake-parts' `self` is the flake's own output set, and this
  # module writes a flake output (flake.lib.frappeSecrets). Reading one from the
  # other is an infinite recursion — hence `relDir` below rather than stripping
  # the flake root off `dir`.
  schema = import ../lib/secrets-schema.nix { inherit lib; };

  sshKey = types.strMatching "^(ssh-ed25519|ssh-rsa|ecdsa-sha2-[a-z0-9-]+) [A-Za-z0-9+/]+=*( .*)?$" // {
    name = "sshPublicKey";
    description = "SSH public key (authorized_keys format)";
  };

  # A bench-level standard secret. `file` is not configurable: agenix resolves a
  # rule by the literal path string and writes output relative to $PWD, so the
  # .age location and the rules key have to stay in lockstep. Fixing the layout
  # is what makes that guaranteed rather than merely conventional.
  benchRole = role: {
    enable = mkEnableOption "the ${role.file} secret";

    hosts = mkOption {
      type = types.bool;
      default = false;
      description = "Also encrypt this secret to `hostRecipients`.";
    };
  };

  siteModule = types.submodule {
    options = {
      encryptionKey = mkOption {
        type = types.bool;
        default = true;
        description = ''
          Declare `<dir>/<site>/encryption-key.age` — the Frappe encryption key,
          one line. This is the key that decrypts every stored Password and
          API-secret field, so it is also what `services.frappe`'s
          `encryptionKeyFile` wants in production.
        '';
      };

      databasePassword = mkOption {
        type = types.bool;
        default = true;
        description = ''
          Declare `<dir>/<site>/db-password.age`, one line — the production
          counterpart of `services.frappe.sites.<name>.database.passwordFile`.
        '';
      };

      extraConfig = mkOption {
        type = types.bool;
        default = true;
        description = ''
          Declare `<dir>/<site>/site-config.age`, a JSON object deep-merged into
          `site_config.json` — the counterpart of
          `services.frappe.sites.<name>.extraConfigFiles`. Object-storage and
          third-party credentials belong here.
        '';
      };

      developers = mkOption {
        type = types.bool;
        default = true;
        description = ''
          Let `recipients` read this site's secrets, not just `hostRecipients`.

          On by default: reading the production encryption key is what keeps
          encrypted Password and API-secret fields legible after a dev restore.
          Turning it off gives a useful capability tier — a contractor who can
          fetch and restore the database but cannot read the credentials stored
          inside it.
        '';
      };
    };
  };

  extraModule = types.submodule (
    { name, ... }:
    {
      options = {
        format = mkOption {
          type = types.enum [ "env" "raw" "json" ];
          default = "env";
          description = ''
            How the dev shell consumes the plaintext:

            `env`  — `KEY=value` lines, sourced with `set -a` so each becomes a
                     variable. The file is executed by the shell, so it must be
                     shell syntax.
            `raw`  — a single value; `$<var>` is it.
            `json` — a JSON object; left on disk, `$<var>_PATH` points at it.
          '';
        };

        var = mkOption {
          type = types.str;
          default = "frappe_${schema.slug name}";
          defaultText = literalExpression "\"frappe_\${name}\"";
        };

        hosts = mkOption {
          type = types.bool;
          default = false;
        };
      };
    }
  );
in
{
  options.frappe-nix.secrets = {
    enable = mkOption {
      type = types.bool;
      default = cfg.recipients != { };
      defaultText = literalExpression "recipients != { }";
      description = "Wire agenix + agenix-shell into this bench.";
    };

    dir = mkOption {
      type = types.path;
      example = literalExpression "./secrets";
      description = ''
        Directory holding this bench's `.age` files. Write it as a path literal
        relative to your `flake.nix`, e.g. `dir = ./secrets;`.

        The `.age` files are **meant to be committed** — they are age ciphertext,
        and a flake's source tree is exactly its git-tracked files, so an
        untracked one is invisible to the build and the shell reports it missing.
      '';
    };

    relDir = mkOption {
      type = types.str;
      default = baseNameOf (toString cfg.dir);
      defaultText = literalExpression "baseNameOf dir";
      description = ''
        The same directory, expressed relative to the bench root.

        agenix keys its rules by the literal path string and writes output
        relative to `$PWD`, so the rules file has to name `secrets/foo.age`, not
        a `/nix/store` path. That cannot be derived from `dir` by stripping the
        flake root: flake-parts' `self` is the flake's own output set, and this
        module writes one, so reading it here would be an infinite recursion.

        Override only if `dir` is nested (e.g. `dir = ./nix/secrets;` needs
        `relDir = "nix/secrets";`).
      '';
    };

    recipients = mkOption {
      type = types.attrsOf sshKey;
      default = { };
      example = literalExpression ''
        {
          kevin  = "ssh-ed25519 AAAAC3Nza…";
          gideon = "ssh-ed25519 AAAAC3Nza…";
        }
      '';
      description = ''
        Public keys of the people who may read this bench's secrets.

        This is the single source of truth. The agenix rules file is generated
        from it — there is no `secrets.nix` to edit — and the
        `frappe-nix-secrets` check verifies that every `.age` file is actually
        encrypted to exactly these keys. A rotated key therefore cannot sit in
        the rules while the ciphertext still names the key it replaced.

        Attribute names are labels: they are carried into the key line as an SSH
        comment so error messages can name a person instead of a base64 blob.

        After changing this, run `rekey-secrets`.
      '';
    };

    hostRecipients = mkOption {
      type = types.attrsOf sshKey;
      default = { };
      example = literalExpression ''{ lcserver = "ssh-ed25519 AAAAC3Nza…"; }'';
      description = ''
        Public keys of deployment hosts. These are added to the per-site secrets
        — the ones `services.frappe` consumes — but not to developer-only
        secrets unless a secret sets `hosts = true`.

        A host key here is what lets the deployment read the same ciphertext the
        bench declares, instead of a second copy in the server repo that has to
        be rotated in lockstep and silently drifts when it is not.
      '';
    };

    identityPaths = mkOption {
      type = types.listOf types.str;
      default = [
        "$HOME/.ssh/id_ed25519"
        "$HOME/.ssh/id_rsa"
      ];
      description = ''
        Private keys tried when decrypting, in order. Forwarded to
        `agenix-shell.identityPaths`. Shell strings, expanded at runtime — which
        is why the dev shell needs `--no-pure-eval`.
      '';
    };

    # ── the standard roles ──────────────────────────────────────────────────
    backupAccess = benchRole {
      file = "backup-access";
      var = "frappe_backup_access";
    } // {
      enable = mkOption {
        type = types.bool;
        default = cfg.enable;
        defaultText = literalExpression "secrets.enable";
        description = ''
          Declare `<dir>/backup-access.age`: object-store credentials for
          fetching production backups, as a shell env-file.

          Expected contents:

              BACKUPS_URL=https://s3.us-east-005.backblazeb2.com
              BACKUPS_ACCESS_KEY=…
              BACKUPS_SECRET_KEY=…
              BACKUPS_BUCKET=my-backups
              BACKUPS_PREFIX=Backups/      # optional

          Bucket and prefix live in the secret rather than in Nix on purpose:
          they are per-deployment facts that change together with the
          credentials, and keeping them together means `bench restore` needs no
          Nix configuration at all.
        '';
      };
    };

    sites = mkOption {
      type = types.attrsOf siteModule;
      default = { };
      example = literalExpression ''{ "erp.example.com" = { }; }'';
      description = ''
        Per-site secrets. `sites."erp.example.com" = { };` declares all three:

            <dir>/erp.example.com/encryption-key.age   (one line)
            <dir>/erp.example.com/db-password.age      (one line)
            <dir>/erp.example.com/site-config.age      (JSON object)

        The same ciphertext feeds the dev shell and, through
        `flake.lib.frappeSecrets`, `services.frappe`'s `encryptionKeyFile` /
        `database.passwordFile` / `extraConfigFiles`. One declaration, one file,
        both ends.
      '';
    };

    extra = mkOption {
      type = types.attrsOf extraModule;
      default = { };
      description = "Additional bench-specific secrets.";
    };

    # ── read-only, for modules/devenv.nix and the checks ────────────────────
    declared = mkOption {
      type = types.listOf (types.attrsOf types.unspecified);
      internal = true;
      readOnly = true;
      default = if cfg.enable then schema.secretList cfg else [ ];
      description = "Normalised secret list — see lib/secrets-schema.nix.";
    };
  };

  # `mkIf` goes on each attribute, NOT around the whole `config`.
  #
  # `cfg.enable` defaults to `recipients != {}`, and `recipients` is declared in
  # this very module. Wrapping the whole config in `mkIf cfg.enable` means the
  # module system has to force the condition just to learn which options this
  # module defines — which means gathering the definitions of `recipients` —
  # which means evaluating this `config`. Infinite recursion, and it surfaces as
  # a `_module.freeformType` loop several layers away from the cause.
  #
  # Per-attribute, the attribute names are static and each condition is forced
  # only when that option is actually read.
  config = {
    agenix-shell = lib.mkIf cfg.enable {
      inherit (cfg) identityPaths;
      secrets = schema.agenixShellSecrets cfg;
    };

    # Exposed so a deployment can consume the very same ciphertext this bench
    # declares, instead of a second copy in the server repo that has to be
    # rotated in lockstep and silently drifts when it is not:
    #
    #   age.secrets = lib.mapAttrs (_: f: { file = f; mode = "0400"; })
    #     inputs.mybench.lib.frappeSecrets.files;
    #
    #   services.frappe.sites."erp.example.com" = with
    #     inputs.mybench.lib.frappeSecrets.sites."erp.example.com"; {
    #       encryptionKeyFile = config.age.secrets.<…>.path;
    #       …
    #     };
    #
    # Under `lib` rather than a bespoke top-level attribute because `lib` is a
    # recognised free-form flake output — an unknown one draws a warning from
    # `nix flake check` on every run. Note flake-parts types free-form outputs
    # as `unique`, so a consumer that also defines `flake.lib` gets a clear
    # conflict rather than a silent merge.
    # `optionalAttrs`, not `mkIf`: free-form flake outputs are typed `raw`, so
    # the module system never resolves an `mkIf` here — it would be handed
    # straight through and the consumer would read `{ _type = "if"; … }`.
    flake.lib = lib.optionalAttrs cfg.enable {
      frappeSecrets = {
        recipients = schema.labelled cfg.recipients ++ schema.labelled cfg.hostRecipients;

        # { "<site>" = { encryptionKey = <path>; databasePassword = <path>;
        #                extraConfig = <path>; }; }
        sites = lib.mapAttrs (
          siteName: _:
          lib.listToAttrs (
            map (s: lib.nameValuePair s.role s.file) (builtins.filter (s: s.site == siteName) cfg.declared)
          )
        ) cfg.sites;

        # { "<shell var>" = <path to .age>; } — every declared secret, flat.
        files = lib.listToAttrs (map (s: lib.nameValuePair s.var s.file) cfg.declared);
      };
    };
  };
}
