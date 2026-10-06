# The checks frappe-nix's own dev tooling adds to `nix flake check`.
#
# The Python, JSON, TOML and YAML hooks are .pre-commit-config.yaml's, run by
# prek on a commit and by CI's `lint` job. These are the two that cannot be:
# the Nix linters need nix, and ty needs the dev env's interpreter with the
# pinned upstream Frappe in it.
{ pkgs, inputs }:

let
  dev = import ./env.nix { inherit pkgs inputs; };
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

  # nixfmt, statix and deadnix over the tree. Fixtures stand in for third-party
  # benches and templates hold @PLACEHOLDERS@, so neither is Nix that parses;
  # keep these in step with `exclude` in .pre-commit-config.yaml. statix reads
  # statix.toml, and walks the tree itself.
  nix-lint =
    pkgs.runCommand "frappe-nix-nix-lint"
      {
        nativeBuildInputs = [
          pkgs.deadnix
          pkgs.findutils
          pkgs.nixfmt
          pkgs.statix
        ];
      }
      ''
        cp -r ${../.} src
        chmod -R u+w src
        cd src
        find . -name '*.nix' -not -path './tests/fixtures/*' -not -path './templates/*' -print0 > ../nix-files
        xargs -0 nixfmt --check < ../nix-files
        statix check --format errfmt --ignore 'tests/fixtures/*' --ignore 'templates/*' .
        xargs -0 deadnix --fail < ../nix-files
        touch "$out"
      '';

  # The whole tree, not what changed: editing one module can break a caller in
  # another. --python is explicit because the sandbox has no ./env link for
  # ty.toml's default to find.
  ty = pkgs.runCommand "frappe-nix-ty" { nativeBuildInputs = [ dev.devPythonEnv ]; } ''
    cp -r ${../.} src
    chmod -R u+w src
    cd src
    ty check --python ${dev.devPythonEnv}
    touch "$out"
  '';
}
