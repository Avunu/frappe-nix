{
  description = "Reusable Nix infrastructure for Frappe bench projects";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-parts.url = "github:hercules-ci/flake-parts";
    devenv.url = "github:cachix/devenv";
    nix2container = {
      url = "github:nlewo/nix2container";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    pyproject-nix = {
      url = "github:pyproject-nix/pyproject.nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    uv2nix = {
      url = "github:pyproject-nix/uv2nix";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    pyproject-build-systems = {
      url = "github:pyproject-nix/build-system-pkgs";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.uv2nix.follows = "uv2nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    nix-oci = {
      url = "github:dauliac/nix-oci";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    # Decrypts age secrets into a dev shell. Imported by
    # modules/flake-module.nix, so consumers do not declare it themselves.
    #
    # The agenix *CLI* is deliberately not an input: ryantm/agenix destructures
    # `darwin` and `home-manager` positionally in its outputs, so they cannot be
    # `follows = ""`-ed away and would land in every consumer's lock. `ragenix`
    # is in nixpkgs, is a drop-in (same RULES / -e / -r / -d / -i), and ships an
    # `agenix` symlink.
    agenix-shell = {
      url = "github:aciceri/agenix-shell";
      inputs.nixpkgs.follows = "nixpkgs";
      inputs.flake-parts.follows = "flake-parts";
    };
  };

  nixConfig = {
    extra-substituters = [
      "https://devenv.cachix.org"
    ];
    extra-trusted-public-keys = [
      "devenv.cachix.org-1:w1cLUi8dv3hnoSPGAuibQv+f9TZLr6cv/Hm9XgU50cw="
    ];
  };

  outputs =
    { self, nixpkgs, flake-parts, ... }@inputs:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
        "x86_64-darwin"
        "aarch64-darwin"
      ];
      forAllSystems = f: nixpkgs.lib.genAttrs systems (system: f (import nixpkgs { inherit system; }));
      frappeInit = pkgs: import ./lib/init.nix { inherit pkgs; };
    in
    {
      flakeModules.default = ./modules/flake-module.nix;

      nixosModules.default = ./modules/nixos.nix;

      lib = {
        mkFlake =
          {
            inputs ? { },
            ...
          }@consumerArgs:
          config:
          flake-parts.lib.mkFlake {
            inputs = self.inputs // inputs;
          } config;

        overrides = import ./lib/overrides.nix;
      };

      # `nix run github:Avunu/frappe-nix` scaffolds a new bench (bench-init style).
      packages = forAllSystems (pkgs: rec {
        frappe-init = frappeInit pkgs;
        default = frappe-init;

        # The object-store half of `bench restore`, standalone. Exposed because
        # a production host restores too, and its NixOS module otherwise keeps
        # its own copy of the same folder-selection and download logic — which
        # is how the three copies of this got out of step in the first place.
        # `--fetch-only` style use: it prints a JSON manifest and touches no
        # database, so the deployment keeps its own restore half.
        backup-fetch = import ./lib/backup-fetch.nix { inherit pkgs; };
      });

      apps = forAllSystems (pkgs: let
        program = "${frappeInit pkgs}/bin/frappe-init";
        app = {
          type = "app";
          inherit program;
          meta.description = "Scaffold a new frappe-nix bench (bench-init style)";
        };
      in {
        default = app;
        frappe-init = app;
      });

      checks = forAllSystems (pkgs:
        {
          # Frappe-independent: stub SMTP/POP3 servers stand in for Mailpit and
          # the assertions are about which socket the connection landed on.
          devguard = pkgs.runCommand "frappe-devguard-check" { } ''
            cp -r ${./lib/devguard} ./devguard
            chmod -R u+w ./devguard
            ${pkgs.python3}/bin/python ./devguard/tests/test_devguard.py | tee "$out"
          '';

          # The recipient-drift check, tested against real age ciphertext:
          # throwaway SSH keys, `rage` encrypts to a subset of them, and the
          # assertions are about the verdict. No network and no identity of the
          # builder's — an age header names its recipients in the clear, which
          # is the whole reason the check can run offline.
          agecheck = pkgs.runCommand "frappe-nix-agecheck-check" {
            nativeBuildInputs = [ pkgs.rage pkgs.openssh pkgs.python3 ];
          } ''
            cat > agecheck <<EOF
            #!${pkgs.runtimeShell}
            exec ${pkgs.python3}/bin/python3 ${./lib/agecheck.py} "\$@"
            EOF
            chmod +x agecheck
            bash ${./tests/agecheck.sh} "$PWD/agecheck" \
              ${pkgs.rage}/bin/rage ${pkgs.openssh}/bin/ssh-keygen | tee "$out"
          '';

          # A directory tree stands in for a bucket: `mc ls --json` emits the
          # same shape for a local path as for S3, so folder selection, the
          # -partial and -enc cases, the public/private archive split, the
          # download cache and its retention all run with no server and no
          # network.
          backup-fetch = pkgs.runCommand "frappe-nix-backup-fetch-check" {
            nativeBuildInputs = [ pkgs.jq pkgs.minio-client ];
          } ''
            export HOME="$PWD"
            bash ${./tests/backup-fetch.sh} \
              ${import ./lib/backup-fetch.nix { inherit pkgs; }}/bin/frappe-nix-backup-fetch \
              | tee "$out"
          '';

          # Likewise Frappe-independent: stub modules stand in for frappe.app,
          # frappe.database and werkzeug.serving, and the assertions are about
          # which address the server was told to bind.
          unixsock = pkgs.runCommand "frappe-unixsock-check" { } ''
            cp -r ${./lib/unixsock} ./unixsock
            chmod -R u+w ./unixsock
            ${pkgs.python3}/bin/python ./unixsock/tests/test_unixsock.py | tee "$out"
          '';

          # Also Frappe-independent: a fixture stands in for the patch list
          # frappe-bench ships, and the assertions are about what the reconcile
          # leaves in the bench root's patches.txt.
          bench-patches = pkgs.runCommand "frappe-nix-bench-patches-check" {
            nativeBuildInputs = [ pkgs.findutils ];
          } ''
            bash ${./tests/bench-patches.sh} \
              ${import ./lib/bench-patches.nix { inherit pkgs; }}/bin/frappe-nix-bench-patches \
              2>&1 | tee "$out"
          '';

          # Node-independent in the same spirit: a stub yarn stands in for the
          # install, and the assertions are about when a pulled app is judged to
          # have outgrown its node_modules.
          node-modules = pkgs.runCommand "frappe-nix-node-modules-check" {
            nativeBuildInputs = [ pkgs.findutils ];
          } ''
            bash ${./tests/node-modules.sh} \
              ${import ./lib/node-modules.nix { inherit pkgs; }}/bin/frappe-nix-node-modules \
              2>&1 | tee "$out"
          '';
        }
        # edit-secret / rekey-secrets against a real ragenix and real keys.
        // import ./tests/secrets-cli.nix { inherit pkgs; }
        # The `bench restore` script itself, rendered and driven against a
        # fixture bucket and a stub bench.
        // import ./tests/bench-restore.nix { inherit pkgs; }
        # The stale-uv.lock preflight, over a fixture workspace.
        // import ./tests/lock-audit.nix { inherit pkgs; }
        # `frappe-init --migrate` over a synthetic classic bench. Offline, so it
        # is cheap enough to gate PRs on.
        // import ./tests/migrate-classic.nix {
          inherit pkgs;
          frappe-init = frappeInit pkgs;
        }
        # NixOS VM tests (Linux only — runNixOSTest builds a VM).
        // nixpkgs.lib.optionalAttrs pkgs.stdenv.hostPlatform.isLinux {
          migrate-rollback = pkgs.testers.runNixOSTest (
            import ./tests/migrate-rollback.nix { inherit self pkgs; }
          );
          socket = pkgs.testers.runNixOSTest (
            import ./tests/socket.nix { inherit self pkgs; }
          );
        });
    };
}
