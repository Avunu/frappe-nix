# The checks frappe-nix's own dev tooling adds to `nix flake check`.
{ pkgs, inputs }:

let
  dev = import ./env.nix { inherit pkgs inputs; };
  hookSet = import ./hooks.nix {
    inherit pkgs;
    env = dev.devPythonEnv;
  };
  # The git-hooks.nix devenv itself pins, so the sandboxed run and the shell's
  # hooks are the same code and frappe-nix carries no input of its own for it.
  gitHooks = inputs.devenv.inputs.git-hooks.lib.${pkgs.stdenv.hostPlatform.system};
in
{
  # Every hook but uv-lock, which needs the network; a stale dev/uv.lock already
  # fails evaluation of the env these hooks run from (lib/lock-audit.nix).
  pre-commit = gitHooks.run {
    src = ../.;
    inherit (hookSet) excludes;
    hooks = removeAttrs hookSet.hooks [ "uv-lock" ];
  };

  # dev/frappe-ruff.toml and the ruff pin against the Frappe dev/uv.lock pins.
  # A build failure rather than an evaluation one, so `sync-frappe-upstream` can
  # still reach frappeSrc through it once they have drifted.
  frappe-upstream-config =
    pkgs.runCommand "frappe-upstream-config"
      {
        passthru = { inherit (dev) frappeSrc; };
      }
      ''
        mkdir -p root/dev
        cp ${./frappe-ruff.toml} root/dev/frappe-ruff.toml
        cp ${./uv.lock} root/dev/uv.lock
        ${dev.frappeUpstream}/bin/frappe-upstream check ${dev.frappeSrc} root | tee "$out"
      '';
}
