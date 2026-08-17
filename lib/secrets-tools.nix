# Build-time artifacts for the secrets subsystem: the generated agenix rules
# file, the recipient checker, and the pieces lib/scripts.nix needs to render
# `edit-secret` / `rekey-secrets`.
#
# Split out of modules/secrets.nix because that module is top-level, where
# `pkgs` does not exist. Split out of lib/scripts.nix so the derivations are
# built once rather than per-script.
{
  lib,
  pkgs,
  cfg,
  schema,
}:

let
  enabled = cfg.enable or false;

  # ragenix, not ryantm/agenix: it is in nixpkgs (so no new flake input), it is
  # a drop-in — same RULES env var, same -e/-r/-d/-i — and it ships an `agenix`
  # symlink so muscle memory and any existing docs still work. Taking the real
  # agenix as an input would drag nix-darwin and home-manager into every
  # consumer's lock, because its outputs destructure them positionally and they
  # therefore cannot be followed away.
  cli = pkgs.ragenix;

  # The rules file agenix reads, generated from `recipients` so that a rotated
  # key cannot sit in the rules while the ciphertext still names its
  # predecessor. Never written to the worktree — RULES points here.
  rulesFile = pkgs.writeText "frappe-nix-secrets.nix" (schema.rulesText cfg);

  # The same content as JSON, for the checker.
  rulesJSON = pkgs.writeText "frappe-nix-secrets.json" (schema.rulesJSON cfg);

  agecheck = pkgs.writeTextFile {
    name = "frappe-nix-agecheck";
    destination = "/bin/frappe-nix-agecheck";
    executable = true;
    # Not writers.writePython3Bin: it runs its own linter with its own opinions
    # at build time. A shebang'd text file is dependency-free and stable — the
    # same reasoning as lib/workspace-tool.nix.
    text = "#!${pkgs.python3}/bin/python3\n" + builtins.readFile ./agecheck.py;
  };

  # Shell fragment: decrypt every declared secret and make it usable.
  #
  # Sourced on demand by the scripts that need it, NOT from enterShell. Three
  # reasons, all load-bearing:
  #
  #   1. agenix-shell rm -rf's its runtime dir and re-runs rage on every source,
  #      and enterShell runs on every direnv reload — so a passphrase-protected
  #      key would prompt on every file save.
  #   2. agenix-shell exports the plaintext itself, not just the path. At shell
  #      entry that puts credentials in the environment of every process in the
  #      session, `devenv up`'s children included, readable through
  #      /proc/<pid>/environ.
  #   3. A decryption failure becomes an error where the secret is used, with a
  #      message that can say what to do, instead of a warning scrolled past
  #      several minutes earlier.
  loadSecrets =
    installationScript:
    let
      envSecrets = builtins.filter (s: s.format == "env") cfg.declared;
    in
    ''
      # agenix-shell hardcodes "$XDG_RUNTIME_DIR/agenix-shell/<hash>" with no
      # fallback (devenv has one; agenix-shell does not). Unset — the normal
      # state on darwin — that path becomes "/agenix-shell/…", every decrypt
      # fails, and because nothing here runs under `set -e` it fails silently.
      if [ -z "''${XDG_RUNTIME_DIR:-}" ]; then
        XDG_RUNTIME_DIR="''${DEVENV_RUNTIME:-''${TMPDIR:-/tmp}/frappe-nix-$UID}"
        export XDG_RUNTIME_DIR
        mkdir -p "$XDG_RUNTIME_DIR"
      fi

      # The installation script is built with bashOptions = [], so it does not
      # touch our shell options — but a failing `rage` still returns non-zero
      # and would trip our own `set -e`. Decryption failure is diagnosed below,
      # properly; it must not abort here.
      __fnx_errexit=0
      case "$-" in *e*) __fnx_errexit=1; set +e ;; esac
      # shellcheck source=/dev/null
      . "${lib.getExe installationScript}"
      [ "$__fnx_errexit" = 1 ] && set -e
      unset __fnx_errexit

      ${lib.concatMapStringsSep "\n" (s: ''
        if [ -r "''${${s.var}_PATH:-/nonexistent}" ]; then
          set -a
          # shellcheck source=/dev/null
          . "''$${s.var}_PATH"
          set +a
        fi
      '') envSecrets}
    '';

  # Shell fragment: fail with something actionable when a secret is empty.
  requireSecret = var: ''
    if [ -z "''${${var}:-}" ]; then
      echo "frappe-nix: could not decrypt the '${var}' secret." >&2
      echo >&2
      ${lib.getExe' agecheck "frappe-nix-agecheck"} explain ${rulesJSON} "${var}" \
        --root "$FRAPPE_BENCH_ROOT" >&2 || true
      exit 3
    fi
  '';
in
{
  inherit
    enabled
    cli
    rulesFile
    rulesJSON
    agecheck
    loadSecrets
    requireSecret
    ;

  inherit (cfg) declared relDir;

  # Packages the dev shell needs once secrets are on.
  packages = lib.optionals enabled [
    cli
    agecheck
    pkgs.jq
  ];

  # Every declared secret, as `<name> — <purpose>` lines for `edit-secret`'s
  # usage text. The name is what the user passes; it is the .age path with the
  # secrets dir and the extension stripped.
  names = map (s: {
    name = lib.removeSuffix ".age" (lib.removePrefix "${cfg.relDir}/" s.relPath);
    inherit (s) relPath format var;
  }) cfg.declared;
}
