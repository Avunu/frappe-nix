# The `ironclad` Python package (py/ironclad, docs/ironclad/spec.md S10), built
# against the locked nixpkgs' python314 — the interpreter frappe-nix's
# version-16 benches use. Called from ./outputs.nix with
# python314Packages.callPackage.
#
# pythonRuntimeDepsCheck stays on: it fails the build when nixpkgs' jinja2,
# tomlkit or packaging no longer satisfy py/ironclad/pyproject.toml, which is
# the same range the no-Nix `uv tool install` resolves against. The unittest
# suites in py/ironclad/tests run as the check phase.
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
  src = ../../py/ironclad;
in
buildPythonPackage {
  pname = "ironclad";
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
  # test_repo creates throwaway repositories; git wants an identity and a home.
  preCheck = ''
    export HOME="$TMPDIR"
  '';

  pythonImportsCheck = [
    "ironclad"
    "ironclad.cli"
  ];

  meta = {
    description = "The Ironclad platform tools of frappe-nix: managed files, CI gates and release checks for Frappe apps";
    license = lib.licenses.mit;
    mainProgram = "ironclad";
  };
}
