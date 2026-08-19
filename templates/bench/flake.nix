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

  # This flake exposes:
  #   packages.<system>.default  — the built bench package (apps + python + node + assets)
  #
  # Deployment servers import the NixOS module directly from frappe-nix:
  #   imports = [ frappe-nix.nixosModules.default ];
  #   services.frappe.package = benchFlake.packages.x86_64-linux.default;
  #   services.frappe.sites."mysite.example.com" = { ... };

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

        # ── Secrets ──────────────────────────────────────────────────────
        # Uncomment and add your SSH public key, then `edit-secret
        # backup-access` in the dev shell — after that, `bench restore` pulls
        # the latest production backup and restores it, creating the site if it
        # does not exist yet.
        #
        # Top-level, not under perSystem: who may decrypt is a fact about the
        # bench, not about a platform. `.age` files are committed; there is no
        # secrets.nix to maintain. See the frappe-nix README, "Secrets".
        #
        # frappe-nix.secrets = {
        #   dir = ./secrets;
        #   recipients.you = "ssh-ed25519 AAAA… you@host";
        #   # Deployment host keys, added to the per-site secrets only:
        #   # hostRecipients.myserver = "ssh-ed25519 AAAA… root@myserver";
        #   sites."@SITE_NAME@" = { };
        # };

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
