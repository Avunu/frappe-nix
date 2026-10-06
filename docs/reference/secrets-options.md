---
title: Secrets options
description: Every frappe-nix.secrets option, declared at the top level of the flake, with types and defaults.
order: 2
tags: [options, reference, secrets, age]
updated: 2026-10-06
---

These options sit at the flake's top level, not under `perSystem`. Recipients and `.age` paths are facts about the bench rather than about a platform, and agenix-shell's own secret options are top-level for the same reason. The types and defaults were checked against the module by evaluating it. For the concepts, see [Secrets](../development/secrets.md).

| Option                          | Type                    | Default                                           | Notes                                                                                                                                                                                                   |
| ------------------------------- | ----------------------- | ------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `enable`                        | bool                    | `recipients != { }`                               | Wire agenix and agenix-shell into this bench.                                                                                                                                                           |
| `dir`                           | path                    | required                                          | Where the `.age` files live, such as `./secrets`. Write it as a path literal relative to your `flake.nix`.                                                                                              |
| `relDir`                        | str                     | `baseNameOf dir`                                  | The same directory relative to the bench root. Override only if `dir` is nested, for example `dir = ./nix/secrets;` needs `relDir = "nix/secrets";`.                                                    |
| `recipients`                    | attrs of SSH public key | `{ }`                                             | The people who may decrypt. The attribute names become labels in error messages. After changing it, run `rekey-secrets`.                                                                                |
| `hostRecipients`                | attrs of SSH public key | `{ }`                                             | Deployment host keys. Added to the per-site secrets, and to others only where `hosts = true`.                                                                                                           |
| `identityPaths`                 | list of str             | `[ "$HOME/.ssh/id_ed25519" "$HOME/.ssh/id_rsa" ]` | Private keys tried when decrypting, in order. Shell strings, expanded at runtime, which is why the dev shell needs `--no-pure-eval`.                                                                    |
| `backupAccess.enable`           | bool                    | `secrets.enable`                                  | Declare `<dir>/backup-access.age`: the object-store credentials for fetching production backups, as an env-file.                                                                                        |
| `backupAccess.hosts`            | bool                    | `false`                                           | Also encrypt `backup-access.age` to `hostRecipients`.                                                                                                                                                   |
| `sites.<NAME>.encryptionKey`    | bool                    | `true`                                            | Declare `<dir>/<NAME>/encryption-key.age`: the Frappe encryption key, one line. Also what `encryptionKeyFile` wants in production.                                                                      |
| `sites.<NAME>.databasePassword` | bool                    | `true`                                            | Declare `<dir>/<NAME>/db-password.age`, one line. The counterpart of `database.passwordFile`.                                                                                                           |
| `sites.<NAME>.extraConfig`      | bool                    | `true`                                            | Declare `<dir>/<NAME>/site-config.age`, a JSON object deep-merged into `site_config.json`. The counterpart of `extraConfigFiles`.                                                                       |
| `sites.<NAME>.developers`       | bool                    | `true`                                            | Let `recipients` read this site's secrets, not just `hostRecipients`. `false` is a useful tier: someone who can fetch and restore the database but cannot read the credentials stored inside it.        |
| `extra.<NAME>.format`           | `env`, `raw` or `json`  | `"env"`                                           | How the dev shell consumes the plaintext. `env` is `KEY=value` lines, sourced by the shell. `raw` is a single value, `$<var>`. `json` is a JSON object left on disk, with `$<var>_PATH` pointing at it. |
| `extra.<NAME>.var`              | str                     | `"frappe_<NAME>"`                                 | The shell variable name.                                                                                                                                                                                |
| `extra.<NAME>.hosts`            | bool                    | `false`                                           | Also encrypt this secret to `hostRecipients`.                                                                                                                                                           |

## backup-access.age

The env-file `bench restore` reads has these keys:

```text
BACKUPS_URL=https://s3.us-east-005.backblazeb2.com
BACKUPS_ACCESS_KEY=<ACCESS_KEY>
BACKUPS_SECRET_KEY=<SECRET_KEY>
BACKUPS_BUCKET=<BUCKET_NAME>
BACKUPS_PREFIX=Backups/      # optional
```

The bucket and prefix live in the secret rather than in Nix on purpose. They are per-deployment facts that change together with the credentials, and keeping them together means `bench restore` needs no Nix configuration at all.

## Read-only output

When secrets are enabled, the flake also exposes `lib.frappeSecrets` with `recipients`, `sites` and `files`, so a deployment can read the same ciphertext. See [Use the same secrets on the server](../development/secrets.md#use-the-same-secrets-on-the-server).
