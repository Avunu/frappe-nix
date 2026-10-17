# Scaffolder / migrator for frappe-nix benches — the `nix run` entry point.
#
# Produces a `frappe-init` executable that either writes a new bench (a thin
# frappe-nix wrapper flake + apps as git submodules, with python/node pinned
# from lib/frappe-presets.json), converts an existing `bench init` bench in
# place, or puts a dev environment in one Frappe app's repository. The mode is
# detected from the target directory.
#
# The scaffolder itself is Python, `frappe_nix_tools.frappe_init` in
# frappe-nix-tools (py/frappe_nix_tools, PLAN.md Track D, D2), on
# frappe-nix's tool interpreter. This file is its glue: the store paths it
# needs, as the JSON FRAPPE_NIX_CLI_CONFIG names, and the commands it runs on
# PATH.
{
  pkgs,
  # For frappe-nix-build-deps-sync on PATH, which derives a new bench's
  # [tool.uv.extra-build-dependencies] block from the lock frappe-init writes
  # (lib/build-deps.nix). Null leaves the template's entries as they are.
  python-devops-nix ? null,
}:

let
  inherit (pkgs) lib;

  workspaceTool = import ./workspace-tool.nix { inherit pkgs; };

  # frappe-nix-tools without its test suite, which frappe-nix's own checks run
  # (packages.frappe-nix-tools): a test that fails on one platform must not make
  # frappe-init unbuildable for users who never opt in (S35).
  standards = import ./standards/outputs.nix { inherit pkgs; };
  frappeNixTools = standards.frappeNixTools.overridePythonAttrs { doCheck = false; };
  frappeNixTool = import ./standards/tools/frappe-nix.nix { inherit pkgs lib frappeNixTools; };

  # `frappe-nix` (the bin/ wrapper, which propagates nothing), for `--sync`,
  # `--check` and `--standards`. Sync's phase B also runs uv, yarn and node
  # (tools/uv.lock, yarn.lock), so they come along: `nix run …#frappe-init --
  # --sync` then needs only nix and git (spec §3.3), and the dev-shell
  # re-entry is only the fallback for a bare `frappe-nix sync`. Node, yarn and
  # Python 3.14 are on the PATH of the frappe-nix process only: every other
  # frappe-init mode (bench init and migrate, whose `uv lock` picks an
  # interpreter from PATH, and app mode without opt-in) keeps main's (S35).
  frappeNix = pkgs.writeShellApplication {
    name = "frappe-nix";
    runtimeInputs = with pkgs; [
      nodejs_24
      yarn
      # uv lock --project tools resolves for the tools project's requires-python.
      python314
    ];
    text = ''
      exec ${lib.getExe frappeNixTool} "$@"
    '';
  };

  # frappe-init runs as `python -m` in an environment with frappe-nix-tools in
  # it, not as a console script of the package: nixpkgs' wrapper for those puts
  # python3.14 and the package's own bin/ (an unwrapped frappe-nix, without
  # node and yarn) on PATH ahead of the caller's, which every git, uv and
  # frappe-nix that frappe-init runs would inherit. An environment's interpreter
  # changes no PATH. -I, because frappe-init runs inside benches and app
  # repositories: neither their directory nor a dev shell's PYTHONPATH may put a
  # module ahead of frappe-nix-tools' own.
  python = frappeNixTools.pythonModule.withPackages (_: [ frappeNixTools ]);

  # What frappe-init reads (frappe_init/config.py); it refuses a missing or
  # an unknown key.
  config = pkgs.writeText "frappe-init.json" (
    builtins.toJSON {
      presets = "${./frappe-presets.json}";
      template = "${../templates/bench}";
      appTemplate = "${../templates/app}";
    }
  );
in
pkgs.writeShellApplication {
  name = "frappe-init";
  runtimeInputs =
    with pkgs;
    [
      git
      uv
      workspaceTool
      frappeNix
    ]
    ++
      lib.optional (python-devops-nix != null)
        (import ./build-deps.nix { inherit pkgs python-devops-nix; }).sync;
  text = ''
    FRAPPE_NIX_CLI_CONFIG=${config} exec ${python}/bin/python3 -I -m frappe_nix_tools.frappe_init "$@"
  '';
  meta = {
    description = "Scaffold a frappe-nix bench, migrate a bench to it, or set up a Frappe app's repository";
    mainProgram = "frappe-init";
  };
}
