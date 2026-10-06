# frappe-nix's own development environment, built by the same lib/python.nix a
# bench is: uv2nix over dev/uv.lock, so Frappe is the upstream revision that lock
# pins and a stale lock fails evaluation with lib/lock-audit.nix's sentence.
#
# Shared by the dev shell (dev/devenv.nix) and the flake checks, so the hooks a
# commit runs and the hooks CI runs see the same ruff, ty and Frappe.
{ pkgs, inputs }:

let
  inherit (pkgs) lib;

  python = pkgs.python314;

  envs = import ../lib/python.nix {
    inherit pkgs lib python;
    workspaceRoot = ./.;
    benchName = "frappe-nix";
    inherit (inputs) pyproject-nix pyproject-build-systems uv2nix;
    extraOverrides = (import ../lib/overrides.nix).mysqlclient {
      inherit pkgs;
      inherit (pkgs) mariadb;
    };
    # runtime/ is reached as ../runtime from dev/; the shell exports the root.
    editableRoot = "$FRAPPE_NIX_DEV_ROOT";
    lockAuditRelock = ''
      dev/pyproject.toml declares something dev/uv.lock does not carry. Re-lock:

          uv lock --project dev
    '';
    # No grafts: devguard, unixsock, journald and nodebuild are put on the path
    # from the working tree (PYTHONPATH, ty.toml extra-paths), so an edit is live
    # instead of shadowed by a copy in the store.
  };
in
{
  inherit python;
  inherit (envs) devPythonEnv pythonSet;

  # The upstream Frappe source dev/uv.lock pins, unpacked: the archive uv2nix
  # builds frappe from. Its pyproject.toml and .pre-commit-config.yaml are what
  # dev/frappe-ruff.toml and the ruff pin track.
  frappeSrc = pkgs.runCommand "frappe-upstream-src" { } ''
    mkdir "$out"
    tar -xzf ${envs.pythonSet.frappe.src} --strip-components=1 -C "$out"
  '';

  # dev/frappe-upstream.py: pin / sync / check.
  frappeUpstream = pkgs.writeShellScriptBin "frappe-upstream" ''
    exec ${pkgs.python3.withPackages (ps: [ ps.tomlkit ])}/bin/python3 ${./frappe-upstream.py} "$@"
  '';

  # Working-tree roots the language server and interpreter search before
  # site-packages, relative to the repository root.
  sourceRoots = [
    "runtime/src"
    "lib/devguard"
    "lib/unixsock"
    "lib/journald"
    "lib/nodebuild"
  ];
}
