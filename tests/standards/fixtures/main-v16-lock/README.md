# main-v16-lock

The `nix/uv.lock` that `nix run .#relock` writes for
`tests/standards/fixtures/lock-check-app` (a version-16 app-mode app that has
not opted in) with frappe-nix `main` at e97d588, before N1. `uv lock` (uv
0.12.17) over the root `lib/app-workspace.nix` generated there, nothing else.

It has `coverage` and `unittest-xml-reporting` as packages, through the app's
`test` extra, the way every version-16 lock has them through frappe's, but its
root package does not list them in its `dev` group. `standards-relock-main-lock`
(tests/standards/runtime.nix) checks that this branch's generated root for the
same app is `main`'s and that `uv lock --check` passes on it: upgrading
frappe-nix leaves such an app's lock current, and a relock rewrites nothing.

Never regenerate it with this branch's `relock`: what it stands for is a lock
an existing app committed before upgrading frappe-nix.
