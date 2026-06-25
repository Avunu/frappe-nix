We need to refactor the frappe-nix module with a shift in paradigms and the (re)enforcement of the following assumptions. Frappe-nix always serves as an upstream module to be consumed by an individual "frappe-nix bench" repo which provides a distinct set of frappe applications and/or Python Env customizations. The module facilitate the development environment, as well as deployment outputs in the form of buildable OCI containers and systemd-based NixOS module services. The actual "site" part of the bench is useful for facilitating the development environment, but is NOT part of the deployment paradigm: each deployment, whether systemd or OCI based, must declare which sites are being served and their particular parameters. This is the largest change in directions. The current deployment bakes some site assumptions into the deployment itself, but is quite ambivalent about which are declared versus part of the build. This gives us the lackluster control surface like this:

```nix
services.frappe.benchRoot = benchInput.packages.x86_64-linux.benchRoot;
services.frappe.database.createLocally = true;
services.frappe.defaultSite = "erpnext.littlecocalico.com";
services.frappe.enable = true;
services.frappe.nginx.enable = true;
services.frappe.pythonEnv = benchInput.packages.x86_64-linux.prodPythonEnv;
services.frappe.redis.createLocally = true;
```

What we should target is a nixos deployment module which mirrors other nixos core module multi-tenancy configs, e.g.:
```nix
services.frappe.enable
services.frappe.sites.<site>.enable
services.frappe.sites.<site>.siteDir
services.frappe.sites.<site>.database.createLocally
services.frappe.sites.<site>.database.host
services.frappe.sites.<site>.database.name
services.frappe.sites.<site>.database.passwordFile
services.frappe.sites.<site>.database.port
services.frappe.sites.<site>.database.socket
services.frappe.sites.<site>.database.user
services.frappe.sites.<site>.encryptionKey
services.frappe.sites.<site>.redis.host
services.frappe.sites.<site>.redis.port
services.frappe.sites.<site>.redis.socket
services.frappe.sites.<site>.redis.user
services.frappe.sites.<site>.extraConfig.<key>.<value>
services.frappe.sites.<site>.extraConfigFiles (e.g. [config.age.secrets.erp_cloud_storage_settings.path])
```

At first glance this may not seem to fit well with the frappe framework assumptions, but in reality an incredible amount of flexibility emerges when you survey the frappe-specific environment variables combined with the possibility for the nix engine to synthesize a `site-config.json` file per site. (not sure how this will work with an OCI target, but that's not our immediate concern.)

The bottom line is that each deployable frappe-nix bench defines a distinct stateless mutable software stack for which the stateful parts are the consumed deployment parameters. The deployment should not specify `benchRoot` or `pythonEnv` since those are already defined by the development environment. The deployment should specificly target which particular sites are being served and facilitate their stateful storage (both file and database). Since Frappe consumes and respects a distinct `FRAPPE_BENCH_ROOT` versus `SITE_PATH`, the bench/apps can remain in the nix store, and the site path becomes the stateful local site storage.

One of the most significant reprecusions of this direction are that each frappe-nix repository should not only expose a nixos-module suitable for deployment specifically a PACKAGE which is built by nix containing the final BUILT environment representing any stage of development. This must be threaded together carefully since the current frappe-nix module allows for imperative itterations of the local packagesets/app-versions/etc in development (e.g. `bench update`, `bench build`, `bench get-app`, `bench install-app`, etc), but following a commit frappe-nix must define how to build/package the bench in it's resultant state into an immutable deployable format. The production build, whether OCI or NixOS/SystemD should not have a stateful python environment or run `bench build` in deployment, the build should be facilitated by `nix build` as a proper nix output which is ready for deployment in whatever form is desired.