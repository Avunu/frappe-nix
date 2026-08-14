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

  pythonSet =
    (pkgs.callPackage pyproject-nix.build.packages {
      inherit python;
    }).overrideScope
      (
        lib.composeManyExtensions [
          pyproject-build-systems.overlays.default
          overlay
          extraOverrides
        ]
      );

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
  prodPythonEnv = withGrafts "prod" (
    pythonSet.mkVirtualEnv "${benchName}-bench-prod-env" (
      lib.filterAttrs (name: _: name != rootPkgName) workspace.deps.default // rootDepsAttr
    )
  );

  # Development: adds editable overlay so workspace packages resolve from source
  editablePythonSet = pythonSet.overrideScope (
    lib.composeManyExtensions [
      (workspace.mkEditablePyprojectOverlay {
        root = "$REPO_ROOT";
      })
      (final: prev: {
        ${rootPkgName} = prev.${rootPkgName}.overrideAttrs (old: {
          nativeBuildInputs = (old.nativeBuildInputs or [ ]) ++ [
            final.editables
          ];
        });
      })
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
  devPythonEnv = withGrafts "dev" baseDevPythonEnv;

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
