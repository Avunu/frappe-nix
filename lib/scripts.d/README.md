# lib/scripts.d

Dev-shell scripts that live in their own file instead of `lib/scripts.nix`
(docs/ironclad/spec.md §1.2, §5). `lib/scripts.nix` merges every `*.nix` file
here, in name order, over its own scripts. A file that redefines a script,
`lib/scripts.nix`'s own or another file's, fails evaluation.

Each file is a function of the arguments `lib/scripts.nix` was called with,
plus its shared snippets, and returns an attrset of devenv scripts:

```nix
{ lib, appMode, atBench, ... }:
lib.optionalAttrs appMode {
  frappe-test = {
    exec = ''
      ${atBench}
      …
    '';
    description = "Run the app's tests the way CI does.";
  };
}
```

The arguments: `lib`, `pkgs`, `appsWithNode`, `benchBin`, `secrets`,
`nodeModulesBin`, `nodeVerifyBin`, `pythonBin`, `nodeLocksBin`,
`nodeNestedFrontendExcludes`, `restore`, `offlineMigrate`, `appMode`, `lockDir`,
and the snippets `atBench` (cd to the bench root), `atRepo` (cd to the
repository), `siteFlag` (sets `$SITE_FLAG`), `offlineMigrateEnv`,
`workspaceBin`, `registerWorkspaceMember`, `refreshNodeModules`,
`refreshNodeModulesSoft`, `regenNodeLocks`, `regenNodeLocksSoft` and
`syncRegistry` (each documented where `lib/scripts.nix` defines it). Take
`...`, so a new argument never breaks an existing file. A snippet added to
`lib/scripts.nix` for drop-ins joins `dropInArgs` there.

| File | Owner |
| --- | --- |
| `frappe-test.nix`, `frappe-rename-app.nix` | N1 |
| `frappe-demo.nix`, `frappe-shots.nix` | N5 |
