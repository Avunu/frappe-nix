{
  description = "Frappe's Python runtime, extracted from upstream as a standalone package";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

    # The Frappe commit src/ is extracted from. Pinned to a SHA, never a branch:
    # the branch this work was written on was deleted after its rebase-merge.
    # Keep in step with FRAPPE_REF in UPSTREAM.
    frappe-upstream = {
      url = "github:frappe/frappe/757f127a10";
      flake = false;
    };
  };

  outputs =
    { self, nixpkgs, frappe-upstream }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
        "x86_64-darwin"
        "aarch64-darwin"
      ];
      forAllSystems = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
    in
    {
      # For a nixpkgs-style consumer. frappe-nix instead points its uv2nix
      # `srcOverrides.frappe-runtime` at this flake, so the package is built by
      # the bench's own python set with the bench's own interpreter.
      overlays.default = final: prev: {
        python3Packages = prev.python3Packages.overrideScope (
          pyFinal: _pyPrev: { frappe-runtime = pyFinal.callPackage ./package.nix { }; }
        );
      };

      packages = forAllSystems (pkgs: rec {
        frappe-runtime = pkgs.python3Packages.callPackage ./package.nix { };
        default = frappe-runtime;
      });

      checks = forAllSystems (pkgs: {
        package = self.packages.${pkgs.stdenv.hostPlatform.system}.frappe-runtime;

        # src/ is committed rather than generated at build time, because uv builds
        # this repo from a git source in a sandbox with no network. This check is
        # what keeps that honest: re-run the extraction against the pinned upstream
        # and require the result to be byte-identical to what is committed. It
        # fails if someone hand-edits src/ instead of adding a patch, or if the
        # pin moves without a sync.
        drift =
          pkgs.runCommand "frappe-runtime-drift"
            {
              nativeBuildInputs = with pkgs; [
                bash
                patch
                diffutils
              ];
            }
            ''
              cp -r ${
                nixpkgs.lib.fileset.toSource {
                  root = ./.;
                  fileset = nixpkgs.lib.fileset.unions [
                    ./scripts
                    ./overlay
                    ./patches
                    ./src
                  ];
                }
              } repo
              chmod -R +w repo

              # extract.sh wants a Frappe tree; give it only the parts it reads.
              mkdir -p upstream/frappe
              cp -r ${frappe-upstream}/frappe/realtime upstream/frappe/
              cp ${frappe-upstream}/frappe/asgi.py ${frappe-upstream}/frappe/runner.py upstream/frappe/

              OVERLAY=$PWD/repo/overlay/frappe_runtime PATCHES=$PWD/repo/patches \
                bash repo/scripts/extract.sh "$PWD/upstream" "$PWD/regenerated"

              if ! diff -ru repo/src/frappe_runtime regenerated/frappe_runtime; then
                echo
                echo "src/ does not match the pinned upstream plus patches/." >&2
                echo "Run scripts/sync-upstream.sh <frappe-checkout> and commit the result." >&2
                exit 1
              fi
              touch $out
            '';
      });

      devShells = forAllSystems (pkgs: {
        default = pkgs.mkShell {
          packages = with pkgs; [
            python3
            git
            patch
          ];
        };
      });
    };
}
