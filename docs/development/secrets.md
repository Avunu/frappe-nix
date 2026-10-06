---
title: Secrets
description: Keep a bench's credentials as age-encrypted files in the repository, decrypt them into the dev shell, verify who can read them and reuse the same ciphertext on the server.
order: 3
tags: [secrets, age, agenix, security]
updated: 2026-10-06
---

A bench's credentials, meaning the site encryption key, the database password and the object-store keys, live in `.age` files encrypted with [age](https://github.com/FiloSottile/age). They are committed to the repository and decrypted into the dev shell by [agenix-shell](https://github.com/aciceri/agenix-shell). frappe-nix imports agenix-shell itself, so a consuming flake declares only this:

```nix
frappe-nix.secrets = {
  dir = ./secrets;
  recipients = {
    alice = "ssh-ed25519 AAAAC3Nza…";
    bob   = "ssh-ed25519 AAAAC3Nza…";
  };
  hostRecipients.myserver = "ssh-ed25519 AAAAC3Nza…";
  sites."erp.example.com" = { };
};
```

The options sit at the flake's top level, not under `perSystem`. Recipients and `.age` paths are facts about the bench rather than about a platform, and agenix-shell's own secret options are top-level for the same reason. Every option is in the [secrets options reference](../reference/secrets-options.md).

> [!IMPORTANT]
> **`.age` files are meant to be committed.** They are ciphertext, and a flake's source tree is exactly its git-tracked files, so an untracked secret is invisible to the build and the shell reports it missing. `edit-secret` stages new ones for you.

## The fixed layout

With one site, that declaration names four secrets on a fixed layout:

| File                                | Shape       | What consumes it                        |
| ----------------------------------- | ----------- | --------------------------------------- |
| `secrets/backup-access.age`         | env-file    | `bench restore`'s fetch                 |
| `secrets/<SITE>/encryption-key.age` | one line    | `services.frappe`'s `encryptionKeyFile` |
| `secrets/<SITE>/db-password.age`    | one line    | `database.passwordFile`                 |
| `secrets/<SITE>/site-config.age`    | JSON object | `extraConfigFiles`                      |

Each additional site adds its own three. The shapes are the ones `services.frappe` already consumes, so the same ciphertext can serve the deployment. For anything else, `frappe-nix.secrets.extra.<NAME>` declares `secrets/<NAME>.age` in the `env`, `raw` or `json` format.

In the dev shell each secret is available as `$<var>`, or for the JSON format as a file at `$<var>_PATH`. A secret whose `.age` file does not exist yet is left out of the shell on purpose, so declaring a secret and then creating it, which is the documented first run, does not break the flake. `check-secrets` still reports it as missing and says which command writes it.

### Who can read what

`recipients` are the people. `hostRecipients` are deployment hosts, and they are added to every per-site secret. They are added to `backup-access` and to extra secrets only when you set `hosts = true` on them.

A site can set `developers = false` to keep its secrets host-only. That is a useful tier: it lets someone restore the database without being able to read the credentials stored inside it.

## There is no secrets.nix

agenix normally reads a committed rules file listing who may decrypt what. frappe-nix generates that file into the store instead and points agenix's `RULES` at it, because a hand-maintained one can be edited without re-encrypting anything and nothing notices. That is not hypothetical: in the bench this was built for, a rotated key sat in the rules for months while the ciphertext still named the key it replaced, and the person it was rotated for could not decrypt anything.

So the recipient list in `flake.nix` is the only place it is written down, and `check-secrets` proves the ciphertext agrees:

```text
$ check-secrets
secrets/backup-access.age: not encrypted to 1 declared recipient(s):
      KATJVw  bob
    Someone who can still decrypt it must run:  rekey-secrets
```

It works offline and needs no private key. An age header names its recipients in the clear, and an SSH recipient's tag is derivable from the public key alone. `check-secrets <NAME>` turns that around and explains why _your_ key cannot open a particular secret.

After changing `recipients`, run `rekey-secrets` and commit the result.

## Set up backup access

`setup-backup-access` asks five questions instead of making you remember five variable names and the quoting rules for a file the shell will source:

```text
┌────────────────────────────────────────────────────────┐
│ Backup access for mybench                              │
│                                                        │
│ These are the object-store credentials `bench restore` │
│ uses to find and download production backups.          │
└────────────────────────────────────────────────────────┘

Endpoint URL
> https://s3.us-east-005.backblazeb2.com
…
Test these against the bucket now? [Yes]
✓ 34 backup(s) found; newest is 20260814_000042
```

It offers to list the bucket before encrypting anything, because a typo in an access key is otherwise a mystery several minutes into the first restore. Run it again to edit: existing values are pre-filled, and a blank secret key keeps the stored one, so rotating an access key does not mean re-typing a secret that has not changed.

For anything scripted, pipe the env-file in instead:

```bash
edit-secret backup-access <<'ENV'
BACKUPS_URL=https://s3.us-east-005.backblazeb2.com
BACKUPS_ACCESS_KEY=<ACCESS_KEY>
BACKUPS_SECRET_KEY=<SECRET_KEY>
BACKUPS_BUCKET=<BUCKET_NAME>
BACKUPS_PREFIX=
ENV
```

`BACKUPS_PREFIX` is optional. The bucket and prefix live in the secret rather than in Nix on purpose: they are per-deployment facts that change together with the credentials, and keeping them together means `bench restore` needs no Nix configuration at all.

Once backup access exists, see [Restore a production backup](restore-from-production.md).

## Use the same secrets on the server

A bench that declares secrets exposes them as `lib.frappeSecrets`, so a deployment reads the same ciphertext instead of keeping a second copy in the server repository that has to be rotated in lockstep and silently drifts when it is not. It carries:

- `recipients`: every developer and host key, labelled;
- `sites.<SITE>`: the paths of that site's `encryptionKey`, `databasePassword` and `extraConfig` files;
- `files`: every declared `.age` file, flat, keyed by its shell variable name.

On a NixOS host that decrypts with [agenix](https://github.com/ryantm/agenix) you can feed `files` straight to `age.secrets`, and point a site's options at the decrypted paths:

```nix
age.secrets = lib.mapAttrs (_: f: { file = f; mode = "0400"; })
  inputs.mybench.lib.frappeSecrets.files;

services.frappe.sites."erp.example.com" = {
  encryptionKeyFile = config.age.secrets.<NAME>.path;
  # …
};
```

Add the host's public key to `hostRecipients` and run `rekey-secrets`, and that host can decrypt the per-site secrets. See [Run Frappe as a NixOS service](../production/nixos-service.md#secrets).

## When something is wrong

- **The shell reports a secret missing, but the file is on disk.** It is untracked or ignored. Run `git add secrets/<FILE>.age`. The scaffolder's `.gitignore` block deliberately does not exclude `.age` files, and it refuses to proceed if a rule excludes them.
- **`check-secrets` lists a recipient the ciphertext is not encrypted to.** Someone who can still decrypt it must run `rekey-secrets`.
