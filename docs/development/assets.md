---
title: Asset builds
description: How frappe-nix makes Frappe's esbuild pipeline resolve packages the way apps assume, which target it compiles for, and how it heals a stale assets.json.
order: 8
tags: [esbuild, assets, bench-build, watch]
updated: 2026-10-07
---

Frappe's esbuild pipeline compiles each app's JavaScript and CSS bundles. frappe-nix runs it in the dev shell (`bench build` and `watch`) and in `builtBench`, and corrects three things that break apps that work on a stock bench.

## Compile target

`esbuildTarget` (default `es2022`) is the target Frappe's esbuild pipeline compiles bundles for. It is exported as `ESBUILD_TARGET` to `bench build` in the dev shell, to the `watch` process (which, unlike stock `bench watch`, passes it on) and to `builtBench`.

Frappe's own default is es2017. That cannot lower async generators or BigInt literals, which are common in current npm packages such as frappe-react-sdk and temporal-polyfill, so the build fails outright on them.

Object rest and spread are still lowered as es2017 lowers them, whatever the target. Code written against Frappe's default relies on a rest-only parameter accepting `undefined`; see below. Frappe reads `esbuild_target` in `common_site_config.json` ahead of this option, but that file is the operator's and never reaches the Nix build.

## Resolution the way apps assume

[`lib/js/esbuild-preload.js`](../../lib/js/esbuild-preload.js) corrects two things for every build that Frappe's `esbuild/esbuild.js` starts, in the dev shell and in `builtBench` alike.

- **Frappe's `node_modules` comes first.** A bare import that an app's own `node_modules` cannot answer goes through `nodePaths`: every app's `node_modules`, in the order the filesystem lists `apps/`, not `apps.txt`'s. An app that imports a package Frappe provides (`vue`, `pinia`) without declaring it gets the copy from whichever app with its own sorts first, while a package only Frappe ships keeps resolving from Frappe's. One bundle can then carry two copies of a library. For `vue` that means two reactivity systems, and a page that loads its state but never renders it, with no error. A dependency an app declares itself still comes from its own `node_modules`.
- **Object rest and spread are lowered** as Frappe's es2017 default lowers them, whatever `esbuildTarget` says. Natively, a rest-only parameter such as `(name, { ...args }) => …` throws when the argument is left out. The es2017 helper returns `{}`, and code written against that default leaves the argument out. Only that syntax is lowered. Async generators and BigInt, the reason for es2022, stay native.

The preload reaches `bench build` and `bench watch` through `frappe_nodebuild` ([`lib/nodebuild`](../../lib/nodebuild)), grafted into both virtualenvs the way `frappe_unixsock` is. Frappe starts node with `NODE_OPTIONS` from `frappe.build.get_node_env()`, which replaces the caller's, so exporting `--require` beforehand never arrives. `frappe_nodebuild` appends `--require=$FRAPPE_NIX_ESBUILD_PRELOAD`, which the dev shell and `builtBench`'s build phase set and a deployed host does not. Unset the variable for one command to build stock:

```bash
env -u FRAPPE_NIX_ESBUILD_PRELOAD bench build --app <APP>
```

The preload also changes what happens when an app's own build fails. Frappe ends a build by running `yarn build` in every app that has one, in the order the filesystem lists `apps/`, and the first app to fail ends the loop: the apps after it are never started. With `FRAPPE_NIX_KEEP_GOING=1`, which the dev shell sets, the failure is named where it happens, that app's remaining commands are skipped, the other apps are built, and the process exits 1 when they have been. Only a command that ran and exited non-zero is carried past. A signal such as Ctrl-C, or a `yarn` that cannot start, still ends the build. `builtBench` leaves it unset, so an image build stops at the first failure. Unset it for one command to build the way Frappe does:

```bash
env -u FRAPPE_NIX_KEEP_GOING bench build
```

## Vite bundles, registered

Frappe's esbuild writes `sites/assets/assets.json` from what esbuild built, then runs every app's `yarn build`. A bundle an app builds with Vite (`public/dist/js/<name>.bundle.<hash>.js`) gets no key, so `app_include_js = ["<name>.bundle.js"]` or `bundled_asset("<name>.bundle.js")` asks for a file that is not there. After each app's `yarn build` that succeeds, the preload reads the app's Vite manifests and adds a `<name>.bundle.js` / `.css` key for every entry, as Frappe's own `--using-cached` path names the same files. It is the code an app that opted in to the app standards ships as `scripts/vite-register.mjs` for stock benches, and running both changes nothing the second time. It is always on, for every app in app mode, and a registration that fails is a warning, never a failed build. [App assets](../app-standards/assets.md) has the details and the naming conventions.

## Asset-shadow staleness

Some Frappe apps ship their own JS and CSS build tooling that shadows Frappe's own bundle keys in `sites/assets/assets.json`. Carbon-themed desk skins are the case this was found from, though frappe-nix has no knowledge of any specific one.

Frappe's esbuild pipeline writes that file two different ways for the same logical bundle:

1. One keyed by the _source_ entry file's basename. It runs on every `bench build` **and every `bench watch` rebuild**.
2. One keyed by the _built_ file's basename with its content hash stripped. It runs only on `bench build --using-cached`.

An app whose `hooks.py` names the second key can have a `bench watch` rebuild touch only the first, while esbuild's own dist cleanup deletes the file the second key still points at. Frappe's own resolution, `bundled_asset()`, is a bare dict lookup with no existence check, so the browser gets a 404 with no server-side signal. `bench watch` alone is enough to trigger this. No devenv restart and no second app is involved.

### assets.reassert

`assets.reassert.hooks` is the fix. It ships with **zero built-in hooks and names no app**. It is a list of `bench execute`-able dotted paths that you point at your own app's asset-shadow fixup:

```nix
frappe-nix.assets.reassert.hooks = [ "<APP>.<MODULE>.<FUNCTION>" ];
```

Whenever `sites/assets/assets.json` names a bundle file that does not exist on disk, every configured hook runs, in order, against `siteName`. Two triggers cover the two ways this goes stale:

- A `frappe:assets-reassert` task runs the check once at `devenv up`, so a bench that sat idle with a stale `assets.json` heals before the first page load.
- An `assetsWatch` process watches `assets.json` for the writes that `bench watch`'s own rebuilds make, using [fswatch](https://github.com/emcrisostomo/fswatch) for file-change detection, which is portable across Linux and Darwin. It is debounced by `assets.reassert.debounceMs` (default 750 ms).

esbuild's writer truncates and writes with no temp-file-and-rename, so a read can land mid-write. The check retries a failed JSON parse a few times before giving up, and a parse failure is never treated as a missing-file invariant failure, because it would otherwise fire the hooks on every rebuild instead of only when something is actually missing.

`assets-reassert`, a plain devenv script present only when hooks are configured, runs the same check on demand.
