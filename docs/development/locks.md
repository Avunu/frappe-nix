---
title: Dependencies and locks
description: How uv.lock and each app's yarn.lock carry the contract from development to production, what a stale lock looks like, and how the node-locks fallback works.
order: 6
tags: [uv, yarn, lockfiles, relock]
updated: 2026-10-28
---

Two kinds of lock file carry the contract between the developer's imperative commands and the declarative Nix build. You commit both.

| Developer (imperative)                                     | Nix build (declarative)                                                                                                                                                                     |
| ---------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `uv add` and `uv sync`                                     | uv2nix reads `uv.lock`.                                                                                                                                                                     |
| `yarn add` and `yarn install`                              | The app's `yarn.lock` becomes one `fetchurl` per tarball, then `yarn install --offline`. For an app that ships none, `node-locks/<APP>/yarn.lock` from `bench-update --node-locks` is used. |
| `bench build`                                              | `builtBench` runs `bench build` in the sandbox.                                                                                                                                             |
| edits to `apps/*` source                                   | `benchRoot` and `builtBench` copy the source tree.                                                                                                                                          |
| `bench-get-app`, `bench-remove-app`, `bench-update --pull` | `benchRoot` regenerates `sites/apps.{txt,json}` from the members.                                                                                                                           |

Commit `uv.lock`, each app's `yarn.lock` (in the app), `sites/apps.json`, and `node-locks/` for the apps that have no `yarn.lock` of their own. The production environment, `node_modules`, compiled assets, app registry, containers and NixOS deployment are all rebuilt from them.

In [app mode](../scaffolding/app-mode.md) the left column is the same, but the right one reads the _inputs_ rather than the checkout. `nix/uv.lock`, and `nix/node-locks/` for pins without a `yarn.lock`, are what you commit, and `nix run .#relock` is what writes them. The source `builtBench` copies is the pinned flake input, not the writable copy the dev shell put in `.frappe-nix/bench/apps/`. So an edit made inside that copy is a dev-only edit by construction. The app you are developing is the one exception, because it is your repository and `nix build` reads it the same way the shell does.

## Python: uv.lock

`apps/*` are git submodules, and `uv.lock` is a committed, resolved snapshot of what they all declare. When an app gains a dependency, run `uv lock` in the shell. `bench-update --pull` does it for you when any app's `pyproject.toml` moved, so the pull that causes the drift also resolves it. Commit `uv.lock` together with the submodule bumps.

### A stale uv.lock is an evaluation error

Move an app to a commit whose `pyproject.toml` gained a dependency and the two disagree. uv2nix then looks up a name the lock never recorded, and the bench fails to **evaluate**:

```text
error: attribute 'json-repair' missing
at …/uv2nix/build/lib/resolvers.nix:123:23
```

Three things keep that from being a puzzle:

- **`bench-update --pull` re-locks.** It runs `uv lock` when any app's `pyproject.toml` moved, and regenerates the fallback lock of any app without a `yarn.lock` whose `package.json` moved.
- **The error says so.** Before uv2nix resolves anything, frappe-nix audits every declared requirement against the set the resolver will index ([`lib/python.nix`](../../lib/python.nix), on python-devops-nix's `lib.python.lockAudit`) and names the app, the requirement and the fix. Marker-gated and direct-URL requirements are left alone, because they can sit outside a resolution legitimately and a false alarm would be worse than the raw error it replaces.
- **`nix run .#relock` works when nothing else does.** This class of failure blocks evaluation, so the dev shell that carries `uv` is exactly what you cannot open. The `relock` app is deliberately outside every other output's dependency graph and takes its `uv` from nixpkgs, so it still runs.

### relock

`relock` is frappe-nix's one command for re-locking. It regenerates everything a repository commits on frappe-nix's behalf, `uv.lock` (with the managed build-dependency block) and the fallback locks in `node-locks/`. Outside the shell it is `nix run .#relock`. Inside a bench's shell it is also `relock`, the same program, run from the bench root wherever you type it.

```bash
nix run .#relock                                 # uv.lock, then node-locks/
nix run .#relock -- --uv-only                    # only the Python half
nix run .#relock -- --node-locks                 # only the fallback locks
nix run .#relock -- --node-locks hrms/frontend   # force a fallback over the yarn.lock a target ships
nix run .#relock -- --upgrade-package frappe     # any other flag goes to `uv lock`
```

In a bench, `nix run .#relock` must run from the bench root (it checks for `pyproject.toml` and `apps/`). Before it locks, it reconciles `pyproject.toml` with what this frappe-nix expects of a bench root, as the shell does on entry: it adds what is missing and changes nothing already there. Every lock frappe-nix runs (`relock`, `bench-update --pull`, `update-deps` and the shell-entry reconcile) is `uv lock --upgrade-package frappe-runtime`, so the runtime's dependency ranges never stay at the revision the bench first locked. The refresh lands on the frappe-nix revision `flake.lock` pins, not on the head of frappe-nix's branch: for that one lock the runtime's git source gets that `rev`, and it is taken back out of `pyproject.toml` and `uv.lock` afterwards. Locking the same `flake.lock` twice gives the same `uv.lock`, and the runtime's entry moves with `nix flake update frappe-nix`. Without a GitHub `frappe-nix` input in `flake.lock` (a `path:` override, a fork) the lock takes the branch head, as before. In app mode it assembles the workspace itself, writes `nix/uv.lock` and the fallback locks back into the repository, and stages them.

The other commands that re-lock do a part of this. `bench-update --pull` re-locks what a pull moved, `bench-update --node-locks` is `relock --node-locks`, and `update-deps` is `relock --uv-only` followed by `uv sync`, `yarn install` in every app, and the fallback locks.

In app mode the cause is `nix flake update` and not a submodule bump, and the fix is `nix run .#relock` and not `uv lock`. The workspace root there is generated into the store, so there is no bench root of yours to run `uv` in. The audit says that instead. `relock` is also how the first lock is produced: a repository with no `nix/uv.lock` cannot evaluate the dev shell either, and the error names the command.

### Corrected upstream metadata

When a published package declares requirements that cannot be met alongside Frappe, the bench template carries a `[[tool.uv.dependency-metadata]]` entry that corrects them, and the reconcile adds it to existing benches by package name. If a bench has its own entry for the same package, that entry wins. App mode renders its root from the same template.

There is one entry today, for `pypika-stubs`. It is reached through frappe's `dev` extra, which uv locks for every workspace member. Its only release caps `pypika<0.49`, and Frappe 16.53 moved its PyPika fork to 0.51.1. The entry drops the cap and keeps the package, which holds only type hints. Without it, `uv lock` fails with "frappe[dev] depends on pypika>=0.48.9,<0.49.0".

### Build dependencies

A package `uv.lock` has no wheel for is built from its sdist, in the Nix sandbox, with only the build dependencies uv2nix is told about. An old sdist often declares none, so `pyproject.toml` names them under `[tool.uv.extra-build-dependencies]`. That table is derived from `uv.lock` rather than kept by hand (python-devops-nix's `lib.python.buildDeps`, wrapped by [`lib/build-deps.nix`](../../lib/build-deps.nix)):

```toml
[tool.uv.extra-build-dependencies]
# >>> frappe-nix: sdist build deps (managed) >>>
"cairocffi" = ["setuptools", "cffi", "pycparser"]
"docopt" = ["setuptools"]
"frappe-runtime" = ["hatchling"]
# <<< frappe-nix: sdist build deps (managed) <<<
```

- Every package the lock has no wheel for gets `setuptools`.
- frappe-nix adds two entries whatever the lock says: `frappe-runtime` (`hatchling`) and `cairocffi` (`cffi` and `pycparser`).
- A bench adds its own with the `pythonBuildDependencies` option, for a sdist that needs more than setuptools, such as `fuuid = [ "poetry-core" ];`. An entry there replaces the derived one.

The lines between the markers are rewritten after every `uv lock` frappe-nix runs: `nix run .#relock`, `bench-update --pull`, `update-deps`, `bench-get-app`, `bench-new-app`, `bench-remove-app`, the shell-entry reconcile and `frappe-init`. After a `uv lock` of your own, run `frappe-nix-build-deps update` (it is on the shell's PATH); `frappe-nix-build-deps check` exits 1 when the block is out of date. `uv.lock` itself does not record these entries, so it does not change.

In app mode the root `pyproject.toml` is generated, and its block is derived from `nix/uv.lock` at evaluation.

#### Moving a bench to the derived list

A bench scaffolded before the block keeps the list it was given, about 100 hand-kept entries, untouched: a relock prints a one-line hint and changes nothing. The list keeps working. Most of its entries are for packages that now ship wheels, so it is mostly noise, and it never gains an entry for a new sdist-only dependency.

To move:

1. Note the entries in `[tool.uv.extra-build-dependencies]` that are not `["setuptools"]` and that frappe-nix does not add itself (`frappe-runtime`, `cairocffi`). Put each in the flake as `pythonBuildDependencies`, for example `jsonata = [ "setuptools" "pybind11" ];`. Leave out one whose package has a wheel in `uv.lock`: uv2nix does not build it.
2. Replace every entry of the table with the two marker lines:

   ```toml
   [tool.uv.extra-build-dependencies]
   # >>> frappe-nix: sdist build deps (managed) >>>
   # <<< frappe-nix: sdist build deps (managed) <<<
   ```

   An entry left outside the markers for a package the block also lists makes `pyproject.toml` invalid TOML (a key defined twice).
3. `nix run .#relock`, then commit `pyproject.toml`.

The derivations of packages installed from a wheel change once (uv2nix adds a package's extra build dependencies to a wheel's build inputs too, and the stock list named many wheels), so the next deploy rebuilds the environment.

## Node: each app's yarn.lock

Each app with a `package.json`, and each nested frontend under it, is a **node target**. A nested frontend is an immediate subdirectory with its own `package.json`, such as `erpnext/banking`, `hrms/frontend` or `commit/dashboard`. A subdirectory the app tracks as a git submodule, such as `hrms/frappe-ui`, is a project of its own and is skipped.

Each target gets a `node_modules` in the bench package, built from the target's own **`yarn.lock`**. Nothing about it is committed to the bench or computed by hand.

1. [`lib/yarn-lock.nix`](../../lib/yarn-lock.nix) reads the lock at evaluation time and turns every tarball in it into one `fetchurl`, by the URL and the `integrity` the lock already states, or the `#sha1` on the URL in an older lock. A git dependency is fetched by its commit with `builtins.fetchGit` at evaluation time, so a cold cache needs the repository reachable the first time anything forces the package, `nix flake check --no-build` included. It is then packed the way nixpkgs' `prefetch-yarn-deps` packs it.
2. The fetched tarballs are linked into an offline mirror under the names nixpkgs' `fixup-yarn-lock` rewrites the lock to, and nixpkgs' own `yarnConfigHook` installs from it: `yarn install --offline --frozen-lockfile --ignore-scripts --ignore-engines --ignore-platform`. This is what nixpkgs does for any yarn 1 project, minus `fetchYarnDeps`, the one fixed-output derivation over the whole mirror whose hash had to be mined out of a failing build.
3. The derivation sees only the target's `package.json`, `yarn.lock`, `.yarnrc` and `.npmrc`. A change to anything else in the app does not rebuild its `node_modules`, and an upstream `yarn.lock` bump refetches only what moved.

Lifecycle scripts are not run in the sandbox. Every native piece a Frappe frontend needs is a platform package or a prebuilt binary, and all platforms' optional binaries are fetched, since they are in the lock.

`nodeOverrides.<TARGET>` is merged into that target's derivation: `postPatch`, `nativeBuildInputs`, `preInstall`, or a `yarnOfflineCache` of your own. The install flags are the hook's.

In the bench tree each target's `node_modules` is a real directory of links into the store, not a link to the store's directory. Vite creates `node_modules/.vite-temp` while bundling an ESM `vite.config`, and only a permission error is tolerated: the sandbox's read-only store answers `EROFS`.

`bench build` is unchanged: `yarn run production`, then `yarn build` in every app with a build script. The dev shell is unchanged too. Its `node_modules` is a plain online `yarn install`, as upstream tooling expects. See [The development shell](README.md#node_modules-in-the-dev-shell).

## The fallback: node-locks/

An app that ships a `package.json` but no `yarn.lock` has nothing to build from, and evaluation says so. `bench-update --node-locks` resolves one for it: `yarn install` in a scratch directory holding nothing but the manifests, on the developer's machine, with the network. It writes the result to `node-locks/<APP>/yarn.lock`, or `nix/node-locks/` in app mode from `nix run .#relock`, with a stamp of the `package.json` it came from.

Commit it. The target builds from it exactly as it would from its own lock.

- `bench-update` and `bench-update --pull` regenerate the fallbacks whose `package.json` moved during the pull. The stamp makes the others free.
- `update-deps` refreshes them after its `yarn install` runs.
- Evaluation warns about a fallback that is older than its manifest.
- An app that ships a `yarn.lock` gets no fallback. One it once needed is reported as unused when the app grows a lock upstream. Remove it with `git rm -r`.

> [!NOTE]
> After a [migration](../scaffolding/migrate.md), if any app ships a `package.json` without a `yarn.lock`, run `bench-update --node-locks` inside the dev shell before the first `nix build`. The migration does not generate the fallback, because it needs the network and yarn.

### A yarn.lock that cannot resolve offline

Occasionally an upstream lock does not cover its own `package.json`, for example a dependency bump that never regenerated the transitive entries. The offline install then fails with:

```text
Couldn't find any versions for "<PACKAGE>" that matches "<RANGE>" in our cache
```

The build log names the remedies next to that line. There are two.

**Leave the frontend out.** Set `nodeNestedFrontendExcludes = [ "<APP>/<SUBDIR>" ];`. The frontend gets no `node_modules` and no assets. A nested frontend is usually built by its _parent_ app's `build` script (`cd banking && yarn build`), and Frappe's esbuild runs every app's `build` script with no opt-out, so the parent's `build` and `postinstall` scripts that name the frontend are dropped from the bench tree's `package.json` too. The build log says so per script.

**Force a fallback.** Run `bench-update --node-locks <APP>/<SUBDIR>`. It resolves a lock seeded from upstream's own: its pins stay and only the gap is filled from the registry. The lock is stamped `forced`, and that target builds from `node-locks/<APP>/<SUBDIR>/yarn.lock` instead of the upstream file. A forced lock follows the upstream `yarn.lock` on `--pull`, the way the others follow `package.json`.

Nested frontends are discovered one level deep.

### Coming from the earlier schemes

`nodeOfflineHashes` and `node-offline-hashes.json` are gone. The option is an error, so it cannot linger silently. So are the `node-locks/<APP>/package-lock.json` directories of the interim npm-based scheme: `bench-update --node-locks` removes those for every app that ships a `yarn.lock`, and generates the `yarn.lock` fallback for any that does not. `nodeOverrides` entries carrying `yarnFlags` or npm attributes should go too. `yarnFlags` was never read, and a warning says so.
