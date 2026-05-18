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

  # Production: workspace members + runtime deps, no dev tools
  prodPythonEnv = pythonSet.mkVirtualEnv "${benchName}-bench-prod-env" (
    lib.filterAttrs (name: _: name != rootPkgName) workspace.deps.default // rootDepsAttr
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

  devPythonEnv = editablePythonSet.mkVirtualEnv "${benchName}-bench-dev-env" (
    lib.filterAttrs (name: _: name != rootPkgName) (
      workspace.deps.default // workspace.deps.groups
    )
    // rootDepsAttr
    // rootDevDepsAttr
  );

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
