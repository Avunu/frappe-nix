# Python environment factory for Frappe bench projects.
#
# Builds production and development Python environments from a uv workspace,
# handling the common pattern of filtering out the root virtual package and
# re-adding its direct dependencies.
#
# Usage:
#   mkPythonEnvs {
#     inherit pkgs lib;
#     python = pkgs.python312;
#     workspaceRoot = ./.;
#     benchName = "pequea";
#     pyproject-nix = inputs.pyproject-nix;
#     pyproject-build-systems = inputs.pyproject-build-systems;
#     uv2nix = inputs.uv2nix;
#     extraOverrides = final: prev: { ... };
#   }

{
  pkgs,
  lib,
  python,
  workspaceRoot,
  benchName,
  pyproject-nix,
  pyproject-build-systems,
  uv2nix,
  extraOverrides ? (_final: _prev: { }),
  # Derivation containing a `frappe_devguard/` package to graft into the
  # development virtualenv, or null. See lib/devguard.
  devguard ? null,
  # Derivation containing a `frappe_unixsock/` package to graft into *both*
  # virtualenvs, or null. See lib/unixsock.
  unixsock ? null,
  # Overrides the "how to re-lock" half of the stale-lock message. See
  # lib/lock-audit.nix; null keeps its bench-mode default.
  lockAuditRelock ? null,
  # `{ <normalized distribution name> = <source>; }` — where a workspace member's
  # sources actually are, when that is not `workspaceRoot/apps/<name>`.
  #
  # App mode's workspace is assembled in the store and its apps/ are mirrors:
  # real directories whose files are symlinks back into the inputs. uv2nix builds
  # each member from its directory, and a *source* path added to the store keeps
  # no references — so those symlinks would dangle inside the sandbox and the
  # build would fail on a pyproject.toml it can plainly see. Pointing src at the
  # input the mirror was made from avoids the import entirely, and keeps the
  # assembled workspace free of the framework's bytes: it is rebuilt every time
  # the app's own source moves, i.e. on every commit.
  srcOverrides ? { },
}:

let
  workspace = uv2nix.lib.workspace.loadWorkspace { inherit workspaceRoot; };

  rootPyproject = builtins.fromTOML (builtins.readFile (workspaceRoot + "/pyproject.toml"));

  # The root package name from pyproject.toml [project].name
  rootPkgName = rootPyproject.project.name;

  # Extract direct runtime dependencies from [project].dependencies
  rootDepNames = map (
    dep: lib.strings.toLower (builtins.head (builtins.match "([A-Za-z0-9_-]+).*" dep))
  ) (rootPyproject.project.dependencies or [ ]);
  rootDepsAttr = lib.genAttrs rootDepNames (_: [ ]);

  # Extract dev-group packages from [dependency-groups]
  rootDevDepNames = map (
    dep: lib.strings.toLower (builtins.head (builtins.match "([A-Za-z0-9_-]+).*" dep))
  ) (
    lib.filter builtins.isString (
      lib.flatten (lib.attrValues (rootPyproject."dependency-groups" or { }))
    )
  );
  rootDevDepsAttr = lib.genAttrs rootDevDepNames (_: [ ]);

  overlay = workspace.mkPyprojectOverlay {
    sourcePreference = "wheel";
  };

  # Last, so it wins over uv2nix's own src and over anything extraOverrides did.
  # Applied to the editable set as well, below.
  srcOverlay =
    _final: prev:
    lib.mapAttrs (name: src: prev.${name}.overrideAttrs (_: { inherit src; })) (
      lib.filterAttrs (name: _: prev ? ${name}) srcOverrides
    );

  pythonSet =
    (pkgs.callPackage pyproject-nix.build.packages {
      inherit python;
    }).overrideScope
      (
        lib.composeManyExtensions [
          pyproject-build-systems.overlays.default
          overlay
          extraOverrides
          srcOverlay
        ]
      );

  # Turn a stale uv.lock into a sentence instead of an "attribute 'x' missing"
  # deep inside uv2nix's resolver. Membership is tested against `pythonSet`
  # because that is precisely the set resolvers.nix indexes.
  lockAudit =
    import ./lock-audit.nix { inherit lib; }
      (
        {
          inherit workspaceRoot rootPyproject;
          hasPackage = name: pythonSet ? ${name};
        }
        // lib.optionalAttrs (lockAuditRelock != null) { relock = lockAuditRelock; }
      );

  # Wraps both virtualenvs: every consumer — the dev shell, benchRoot, the
  # containers, the NixOS module — reaches uv2nix's resolver through one of them.
  assertLockCurrent = env: if lockAudit.missing == [ ] then env else throw lockAudit.message;

  # Packages grafted into a virtualenv's site-packages with a `.pth` bootstrap,
  # so the interpreter runs them at startup — *below* the Frappe app layer, and
  # without an app install or a site_config.json edit.
  #
  # Grafted rather than put on PYTHONPATH because `apps/*` reach sys.path through
  # uv2nix's editable `.pth` files inside the same venv: an interpreter started
  # outside the devenv environment (an editor terminal, `nix run`, CI, a stray
  # `sudo -u`) still imports Frappe, and would otherwise run unpatched.
  #
  # `zzz-` orders them last. install() is called from the `.pth` line rather than
  # the package body so that importing a single submodule cannot re-enter a
  # partially initialised package.
  graftFor =
    kind:
    # frappe_devguard is DEVELOPMENT ONLY, by construction: it stops a dev bench
    # reaching production services, so reaching production is exactly what it
    # must never be able to do. Do not add it to the prod list.
    lib.optional (kind == "dev" && devguard != null) {
      src = devguard;
      module = "frappe_devguard";
    }
    # frappe_unixsock ships to BOTH, deliberately. It carries no policy — only
    # transport corrections for sockets Frappe half-supports — and a socket-mode
    # deployment needs them precisely because it is production. It shares no code
    # with devguard, so this does not weaken the rule above.
    ++ lib.optional (unixsock != null) {
      src = unixsock;
      module = "frappe_unixsock";
    };

  withGrafts =
    kind: env:
    let
      grafts = graftFor kind;
    in
    if grafts == [ ] then
      env
    else
      # postInstall, not postBuild: mkVirtualEnv sets dontBuild and creates the
      # tree from pyprojectMakeVenvHook's installPhase.
      env.overrideAttrs (old: {
        postInstall =
          (old.postInstall or "")
          + lib.concatMapStrings (graft: ''
            cp -r ${graft.src}/${graft.module} "$out/${python.sitePackages}/"
            printf 'import %s; %s.install()\n' ${graft.module} ${graft.module} \
              > "$out/${python.sitePackages}/zzz-${
                lib.replaceStrings [ "_" ] [ "-" ] graft.module
              }.pth"
          '') grafts;
      });

  # Production: workspace members + runtime deps, no dev tools
  prodPythonEnv = assertLockCurrent (
    withGrafts "prod" (
      pythonSet.mkVirtualEnv "${benchName}-bench-prod-env" (
        lib.filterAttrs (name: _: name != rootPkgName) workspace.deps.default // rootDepsAttr
      )
    )
  );

  # Development: adds editable overlay so workspace packages resolve from source
  editablePythonSet = pythonSet.overrideScope (
    lib.composeManyExtensions [
      (workspace.mkEditablePyprojectOverlay {
        # The *bench*, not the git worktree. In a bench repo the two are the same
        # directory, so this is a no-op there; in app mode they are not, and the
        # editable finder resolves `<root>/apps/<name>`, which is a bench path by
        # construction. REPO_ROOT keeps its literal meaning — the worktree where
        # secrets/*.age are tracked and `git add` has to run.
        root = "$FRAPPE_BENCH_ROOT";
      })
      (final: prev: {
        ${rootPkgName} = prev.${rootPkgName}.overrideAttrs (old: {
          nativeBuildInputs = (old.nativeBuildInputs or [ ]) ++ [
            final.editables
          ];
        });
      })
      # After the editable overlay, which rewrites each member's build phases but
      # keeps reading its sources from src.
      srcOverlay
    ]
  );

  baseDevPythonEnv = editablePythonSet.mkVirtualEnv "${benchName}-bench-dev-env" (
    lib.filterAttrs (name: _: name != rootPkgName) (
      workspace.deps.default // workspace.deps.groups
    )
    // rootDepsAttr
    // rootDevDepsAttr
  );

  # Development: the guards (frappe_devguard) plus the socket corrections
  # (frappe_unixsock). See graftFor above for why only one of those two is also
  # in prodPythonEnv.
  devPythonEnv = assertLockCurrent (withGrafts "dev" baseDevPythonEnv);

in
{
  inherit
    pythonSet
    prodPythonEnv
    devPythonEnv
    editablePythonSet
    workspace
    rootPyproject
    rootPkgName
    ;
}
