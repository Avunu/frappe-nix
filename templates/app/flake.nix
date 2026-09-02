{
  description = "@APP_NAME@ — a Frappe app, developed with frappe-nix";

  inputs = {
    frappe-nix.url = "github:Avunu/frappe-nix";
    # flake-parts resolves perSystem `pkgs` from an input literally named `nixpkgs`.
    nixpkgs.follows = "frappe-nix/nixpkgs";

    # The framework, pinned by this repo's flake.lock. `flake = false`: it is a
    # source tree, not a flake. `nix flake update frappe` moves it — follow that
    # with `nix run .#relock`.
    frappe = {
      url = "github:frappe/frappe/@FRAPPE_BRANCH@";
      flake = false;
    };

    # Sibling apps this one needs — its hooks.py `required_apps`, and anything
    # else the dev bench should carry. Declare them here and under
    # `frappe-nix.app.siblings` below, in install order.
    #
    # erpnext = { url = "github:frappe/erpnext/@FRAPPE_BRANCH@"; flake = false; };
  };

  nixConfig = {
    extra-substituters = [ "https://devenv.cachix.org" ];
    extra-trusted-public-keys = [
      "devenv.cachix.org-1:w1cLUi8dv3hnoSPGAuibQv+f9TZLr6cv/Hm9XgU50cw="
    ];
  };

  # Outputs:
  #   devShells.<system>.default — devenv: MariaDB, Redis, web, worker, socketio…
  #   packages.<system>.default  — a built bench with this app's assets compiled in
  #   apps.<system>.relock       — regenerate nix/uv.lock + nix/node-offline-hashes.json
  outputs =
    { frappe-nix, ... }@inputs:
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

        perSystem = _: {
          frappe-nix = {
            enable = true;
            siteName = "@SITE_NAME@";

            app = {
              enable = true;
              frappeVersion = "@FRAPPE_VERSION@";
              frappe = inputs.frappe;
              # siblings = [ { name = "erpnext"; src = inputs.erpnext; } ];

              # src defaults to `self`, name to this repo's [project].name,
              # benchName to that normalized, python/nodejs to the
              # @FRAPPE_VERSION@ preset, and lockDir to ./nix.
            };
          };
        };
      }
    );
}
