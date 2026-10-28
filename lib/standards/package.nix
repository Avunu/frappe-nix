# frappe-nix-tools, the Python package behind the `frappe-nix` command
# (py/frappe_nix_tools, docs/app-standards/spec.md S10), built against the locked
# nixpkgs' python314 — the interpreter frappe-nix's version-16 benches use.
# Called from ./outputs.nix with python314Packages.callPackage and `base`,
# python-devops-nix's lib.
#
# rich is the base's (lib.python.rich, python-devops-nix docs/conventions.md, Tool
# interpreter with rich): nixpkgs' rich with markdown-it-py's checks off and its
# racy test_brokenpipeerror skipped, the one every interpreter that holds this
# package then gets. That rich is never a cache hit, so its suite runs on every
# cold build; `checkRich = false` (with .override) builds it without, as
# frappe-init does (lib/init.nix).
#
# pythonRuntimeDepsCheck stays on: it fails the build when nixpkgs' jinja2,
# tomlkit, packaging, pyyaml, click or rich no longer satisfy py/frappe_nix_tools/pyproject.toml,
# which is the same range the no-Nix `uv tool install` resolves against. The
# unittest suites in py/frappe_nix_tools/tests run as the check phase.
{
  lib,
  base,
  python,
  buildPythonPackage,
  flit-core,
  jinja2,
  tomlkit,
  packaging,
  pyyaml,
  click,
  unittestCheckHook,
  git,
  checkRich ? true,
}:

let
  src = ../../py/frappe_nix_tools;
  baseRich = (base.python.rich { inherit python; }).rich;
  rich = if checkRich then baseRich else baseRich.overridePythonAttrs { doCheck = false; };
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
    pyyaml
    click
    rich
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
    "frappe_nix_tools.bench_cli"
    "frappe_nix_tools.restore.command"
    "frappe_nix_tools.deps.node_modules"
    "frappe_nix_tools.deps.node_locks"
    "frappe_nix_tools.deps.bench_patches"
    "frappe_nix_tools.deps.setup_requirements"
    "frappe_nix_tools.deps.update"
    "frappe_nix_tools.deps.update_deps"
    "frappe_nix_tools.deps.relock"
    "frappe_nix_tools.frappe_init.command"
    "frappe_nix_tools.app_tools.frappe_test"
    "frappe_nix_tools.app_tools.frappe_demo"
    "frappe_nix_tools.app_tools.frappe_shots"
  ];

  meta = {
    description = "frappe-nix's app standards tools: managed files, profiles, CI gates and release checks for Frappe apps";
    license = lib.licenses.mit;
    mainProgram = "frappe-nix";
  };
}
