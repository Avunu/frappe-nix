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

      # NixOS VM tests (Linux only — runNixOSTest builds a VM).
      checks = forAllSystems (pkgs:
        nixpkgs.lib.optionalAttrs pkgs.stdenv.hostPlatform.isLinux {
          migrate-rollback = pkgs.testers.runNixOSTest (
            import ./tests/migrate-rollback.nix { inherit self pkgs; }
          );
        });
    };
}
