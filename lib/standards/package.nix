# frappe-nix-tools, the Python package behind the `frappe-nix` command
# (py/frappe_nix_tools, docs/app-standards/spec.md S10), built against the locked
# nixpkgs' python314 — the interpreter frappe-nix's version-16 benches use.
# Called from ./outputs.nix with python314Packages.callPackage.
#
# pythonRuntimeDepsCheck stays on: it fails the build when nixpkgs' jinja2,
# tomlkit or packaging no longer satisfy py/frappe_nix_tools/pyproject.toml,
# which is the same range the no-Nix `uv tool install` resolves against. The
# unittest suites in py/frappe_nix_tools/tests run as the check phase.
{
  lib,
  buildPythonPackage,
  flit-core,
  jinja2,
  tomlkit,
  packaging,
  unittestCheckHook,
  git,
}:

let
  src = ../../py/frappe_nix_tools;
in
buildPythonPackage {
  pname = "frappe-nix-tools";
  inherit ((lib.importTOML (src + "/pyproject.toml")).project) version;
  pyproject = true;

  src = lib.fileset.toSource {
    root = src;
    fileset = lib.fileset.difference src (lib.fileset.fileFilter (f: f.hasExt "pyc") src);
  };

  build-system = [ flit-core ];

  dependencies = [
    jinja2
    tomlkit
    packaging
  ];

  nativeCheckInputs = [
    unittestCheckHook
    git
  ];
  unittestFlagsArray = [
    "-s"
    "tests"
    "-v"
  ];
  # The suites create throwaway repositories; git wants an identity and a home.
  preCheck = ''
    export HOME="$TMPDIR"
  '';

  pythonImportsCheck = [
    "frappe_nix_tools"
    "frappe_nix_tools.cli"
    "frappe_nix_tools.common.config"
  ];

  meta = {
    description = "frappe-nix's app standards tools: managed files, profiles, CI gates and release checks for Frappe apps";
    license = lib.licenses.mit;
    mainProgram = "frappe-nix";
  };
}
