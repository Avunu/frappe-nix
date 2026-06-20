{
  description = "@BENCH_NAME@ — Frappe bench (frappe-nix)";

  inputs = {
    # apps/* are git submodules; expose their contents to the flake source tree.
    self.submodules = true;
    frappe-nix.url = "github:Avunu/frappe-nix";
    # flake-parts resolves perSystem `pkgs` from an input literally named `nixpkgs`.
    nixpkgs.follows = "frappe-nix/nixpkgs";
  };

  nixConfig = {
    extra-substituters = [ "https://devenv.cachix.org" ];
    extra-trusted-public-keys = [
      "devenv.cachix.org-1:w1cLUi8dv3hnoSPGAuibQv+f9TZLr6cv/Hm9XgU50cw="
    ];
  };

  outputs =
    { self, frappe-nix, ... }@inputs:
    frappe-nix.lib.mkFlake { inherit inputs; } (
      { ... }:
      {
        imports = [ frappe-nix.flakeModules.default ];

        systems = [
          "aarch64-darwin"
          "aarch64-linux"
          "x86_64-darwin"
          "x86_64-linux"
        ];

        perSystem =
          { pkgs, ... }:
          {
            frappe-nix = {
              enable = true;
              benchName = "@BENCH_NAME@";
              siteName = "@SITE_NAME@";
              workspaceRoot = ./.;
              python = pkgs.@PYTHON@;
              nodejs = pkgs.@NODEJS@;

              # Build production OCI images with `nix build .#web` (etc.).
              # Generate the required yarn hashes first: `bench-update --node-hashes`.
              # containers.enable = true;
            };
          };
      }
    );
}
