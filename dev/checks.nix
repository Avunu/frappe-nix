# The checks frappe-nix's own dev tooling adds to `nix flake check`.
#
# The Python, JSON, TOML and YAML hooks are .pre-commit-config.yaml's, run by
# prek on a commit and by CI's `lint` job. ty cannot be: it needs the dev env's
# interpreter with the pinned upstream Frappe in it. The Nix linters are
# python-devops-nix's nix-lint check, registered in flake.nix.
{ pkgs, inputs }:

let
  dev = import ./env.nix { inherit pkgs inputs; };
  grafts = import ../lib/grafts.nix {
    inherit pkgs;
    inherit (inputs) python-devops-nix;
  };
in
{
  # dev/frappe-ruff.toml and the ruff pins against the Frappe dev/uv.lock pins:
  # the one in dev/uv.lock and the one in .pre-commit-config.yaml. A build
  # failure rather than an evaluation one, so `sync-frappe-upstream` can still
  # reach frappeSrc through it once they have drifted.
  frappe-upstream-config =
    pkgs.runCommand "frappe-upstream-config"
      {
        passthru = { inherit (dev) frappeSrc; };
      }
      ''
        mkdir -p root/dev
        cp ${./frappe-ruff.toml} root/dev/frappe-ruff.toml
        cp ${./uv.lock} root/dev/uv.lock
        cp ${../.pre-commit-config.yaml} root/.pre-commit-config.yaml
        ${dev.frappeUpstream}/bin/frappe-upstream check ${dev.frappeSrc} root | tee "$out"
      '';

  # The whole tree, not what changed: editing one module can break a caller in
  # another. --python is explicit because the sandbox has no ./env link for
  # ty.toml's default to find. The graft packages are checked as a build
  # merges them: python-devops-nix's devguard core and _hook.py copied in
  # (lib/grafts.nix), so their imports resolve.
  ty = pkgs.runCommand "frappe-nix-ty" { nativeBuildInputs = [ dev.devPythonEnv ]; } ''
    cp -r ${../.} src
    chmod -R u+w src
    cd src
    ${grafts.copyVendored "."}
    ty check --python ${dev.devPythonEnv}
    touch "$out"
  '';
}
