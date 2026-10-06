---
title: Run Frappe as a NixOS service
description: Deploy one or more Frappe sites on NixOS with the frappe-nix services.frappe module, including secrets, MariaDB, Redis, nginx and the units it generates.
order: 3
tags: [nixos, services-frappe, systemd, deployment]
updated: 2026-10-06
---

`nixosModules.default` is a standalone NixOS module, not a flake-parts module, for multi-tenant production deployment. Use it when you want declarative Frappe or ERPNext sites with systemd units, automatic migrations and journald logging. You build the bench package as described in [Write the flake by hand](../scaffolding/write-the-flake.md).

## How the module works

The module takes one input: a bench package, the `builtBench` that your bench repository exposes as `packages.default`. It derives all interpreters from the package's `passthru`, so Python, Node, the apps and the compiled assets all come from the package. There are no options for interpreters, `pythonEnv`, `nodejs` or bench paths.

Each enabled site gets its own systemd units and its own state directory. A new build of the bench package changes the store path, which restarts the units and triggers a migration.

> [!IMPORTANT]
> The bench repository exposes only the package. You import the NixOS module from frappe-nix itself, in your host configuration, next to the bench package.

## Import the module and declare a site

Add both flakes as inputs to the flake that defines your host, then import the module and point `package` at the bench build:

```nix
{
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    frappe-nix.url = "github:Avunu/frappe-nix";
    bench.url = "github:example/frappe-bench";
  };

  outputs = { nixpkgs, frappe-nix, bench, ... }: {
    nixosConfigurations.<HOST_NAME> = nixpkgs.lib.nixosSystem {
      system = "x86_64-linux";
      modules = [
        frappe-nix.nixosModules.default
        ./frappe.nix
      ];
      specialArgs = { inherit bench; };
    };
  };
}
```

The module's configuration goes in `frappe.nix`. This example keeps secrets as file paths, here from agenix, but any path readable by root works:

```nix
{ config, bench, ... }:
{
  services.frappe = {
    enable = true;
    package = bench.packages.x86_64-linux.default;

    database.createLocally = true;
    redis.createLocally = true;

    sites."site1.example.com" = {
      enable = true;
      database.createLocally = true;
      database.passwordFile = config.age.secrets.db-password.path;
      encryptionKeyFile = config.age.secrets.encryption-key.path;
      nginx.enable = true;
    };
  };
}
```

Each key under `sites` is the site name, which is also its domain. Run `nixos-rebuild switch` to apply it. The full option list is in the [`services.frappe` reference](../reference/nixos-options.md).

## The units it generates

For each enabled site, the module generates these systemd units. With the default unified runtime a site has one long-running unit, `frappe-<SITE>`, and the others are one-shots.

| Unit                                        | Role                                                                                                                                                                                                                 |
| ------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `frappe-init-<SITE>`                        | One-shot. Assembles the runtime bench tree, links the app registry (`sites/apps.txt`, `sites/apps.json`) and assets from the package, and synthesizes `site_config.json` with `jq`, merging base config and secrets. |
| `frappe-db-password-<SITE>`                 | One-shot, with a local database. Sets the database user's password from `database.passwordFile`.                                                                                                                     |
| `frappe-migrate-<SITE>`                     | One-shot. Runs `bench migrate` when the build changes. Snapshots the database first and rolls back on failure. Skips an uninstalled site. See [Operate a deployed site](operations.md#safe-migrations-on-deploy).    |
| `frappe-<SITE>`                             | The unified runtime: web, realtime, jobs and scheduler in one process.                                                                                                                                               |
| `frappe-web-<SITE>`                         | With `runtime.enable = false`: gunicorn, bound to `sites.<SITE>.web.port`.                                                                                                                                           |
| `frappe-scheduler-<SITE>`                   | With `runtime.enable = false`: the background scheduler.                                                                                                                                                             |
| `frappe-socketio-<SITE>`                    | With `runtime.enable = false`: the Node realtime server.                                                                                                                                                             |
| `frappe-worker-{default,short,long}-<SITE>` | With `runtime.enable = false`: background workers, one per queue.                                                                                                                                                    |

The service units start after, and require, their `frappe-init-<SITE>`. The module also enables MariaDB, a Redis instance named `frappe` (unit `redis-frappe`) and nginx when you ask for them. See [The unified runtime](runtime.md) for the process itself.

## Run more than one site

Every site needs its own listener. Give each additional site a distinct `web.port`, or use a socket path:

```nix
services.frappe.sites."site2.example.com" = {
  enable = true;
  package = bench.packages.x86_64-linux.default;
  web.port = 8001;
  database.createLocally = true;
  database.passwordFile = config.age.secrets.site2-db-password.path;
  encryptionKeyFile = config.age.secrets.site2-encryption-key.path;
  nginx.enable = true;
};
```

Setting `package` on a site lets you run, for example, a staging site on a newer bench build than production. If you leave `runtime.enable = false`, also give each site its own `socketio.port`.

## Secrets

The module never puts secrets in the Nix store. On every start, the `frappe-init-<SITE>` unit builds `site_config.json` in three layers:

1. A base file generated from your Nix options: database and Redis settings, plus `extraConfig`. It is written to the store from Nix-declared values and holds no secrets.
2. The database password and encryption key, read from `database.passwordFile` and `encryptionKeyFile` into `db_password` and `encryption_key`.
3. Each file in `extraConfigFiles`, deep-merged last with `jq`, so it can override earlier values.

systemd's `LoadCredential` hands the secret files to the unit, so the service account never needs read access to the originals. The result is written to `<siteDir>/sites/<SITE>/site_config.json` with mode `0600`.

Use `extraConfigFiles` for anything else sensitive, such as object-storage credentials. The file must contain a JSON object:

```json
{
  "example_integration_key": "<SECRET_VALUE>"
}
```

> [!WARNING]
> `site_config.json` is regenerated on every start of the init unit. A hand edit does not survive the next restart. Put non-secret settings in `extraConfig` and secrets in `extraConfigFiles`. `sites/common_site_config.json` is different: the module copies it from the package once, if the bench ships one, and never touches it again.

If your bench declares its secrets with [frappe-nix.secrets](../development/secrets.md), a deployment can read the same ciphertext through `lib.frappeSecrets` instead of keeping a second copy.

## Database and Redis

### Local MariaDB

With `database.createLocally = true` on a site, the module enables MariaDB, creates the database and user, and sets the server to `utf8mb4` with the `utf8mb4_unicode_ci` collation. The database and user names default to the site name with dots and hyphens replaced by underscores. Override them with `database.name` and `database.user`.

NixOS creates database users without passwords, so the module runs a small `frappe-db-password-<SITE>` unit that sets the password from `database.passwordFile`. Always set `passwordFile` when you use a local database.

By default Frappe connects over the socket at `/run/mysqld/mysqld.sock`.

### External MariaDB

Leave `createLocally` off and describe the server yourself. Setting `socket` to an empty string disables the socket and makes Frappe use `host` and `port`:

```nix
database = {
  host = "db.example.com";
  port = 3306;
  socket = "";
  name = "site1_example_com";
  user = "site1_example_com";
  passwordFile = config.age.secrets.db-password.path;
};
```

You manage the account and its password on an external server. The module does not.

### Redis

`redis.createLocally = true` runs a Redis instance named `frappe` on `127.0.0.1`, port `13000`, under the unit `redis-frappe`. All three site Redis URLs default to that address.

> [!NOTE]
> If you change `services.frappe.redis.port`, update the site URLs too. The defaults are fixed strings and do not follow the port.

## Put nginx in front

`nginx.enable = true` creates a virtual host named after the site. It proxies the app and `/socket.io` to the site's process, serves `/assets/` with a one-year cache header, and serves public uploads from `/files/`. Markup files (`.htm`, `.html`, `.svg`, `.xml`) under `/files/` are forced to download, so an uploaded page cannot run script on your site's origin. Private files are handed back to nginx by Frappe through `/protected/`.

The module does not set up TLS or open firewall ports. Add those with standard NixOS options on the same virtual host name:

```nix
services.nginx.virtualHosts."site1.example.com" = {
  enableACME = true;
  forceSSL = true;
};
security.acme = {
  acceptTerms = true;
  defaults.email = "admin@example.com";
};
networking.firewall.allowedTCPPorts = [ 80 443 ];
```

> [!WARNING]
> The runtime and gunicorn bind `0.0.0.0` when you use `web.port`. Either leave the port closed in the firewall, or use `web.socketPath` so the app has no TCP listener at all.

### Unix sockets behind a tunnel

If a reverse proxy or tunnel connector runs on the same host and terminates TLS, set `web.socketPath` and `nginx.socketPath` to paths inside their own directories, for example `/run/frappe-site1/web.sock`.

The module refuses paths directly in `/run` or `/`, and it creates the directory owned by the service user. That directory is the access control, because nginx makes its own socket world-writable. In this mode nginx takes the client address from the `CF-Connecting-IP` header, so use it only behind a connector that sets that header. `nginx.socketPath` requires `nginx.enable`.

With the default unified runtime, `socketio.port` and `socketio.socketPath` do nothing, and the module warns if you set `socketio.socketPath`. `web.workers` is ignored too, with a warning when it is not at its default of 4, because the runtime sizes web concurrency with `runtime.webThreads`. These options apply only when `runtime.enable = false`. The `socketio.socketPath` option sets a unix socket for the realtime server, needs Frappe 15.46 or newer, and removes the site's last non-loopback TCP listener in that mode.

## Next

The module deploys code and configuration. It does not install a site. Continue with [Operate a deployed site](operations.md) to create or restore one, and to read about migrations, tuning and where things live.
