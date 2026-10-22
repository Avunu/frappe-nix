# What frappe-nix.ide writes for editors (docs/development/editors.md). Pure
# functions of the bench's shape, so tests/ide.nix can check them for both
# modes without entering a shell.
#
# Every path is relative to the workspace root, which is devenv's root: the
# bench root in bench mode, the app's repository in app mode. That keeps the
# files the same on every machine and in every checkout.
#
# Why the apps are listed at all: the dev virtualenv installs each workspace
# member editable, through a .pth file whose one line is
# `import sys; ...; sys.path.append(os.path.expandvars("$FRAPPE_BENCH_ROOT/apps/<app>"))`.
# An interpreter runs it. A static analyser reads only the lines of a .pth
# that are plain paths and skips `import` lines, so without these entries
# `import frappe` is unresolved in every app (or, worse, resolves to some
# other copy of frappe on the analyser's path).
{ lib }:
rec {
  # The app roots, in PYTHONPATH's order: what the shell puts on sys.path,
  # relative to the workspace root. In app mode the app under development is
  # left out: it is the workspace root itself, which every analyser already
  # searches first, and apps/<it> is only a symlink back to it.
  extraPaths =
    {
      appNames,
      benchDir ? null,
      devApp ? null,
    }:
    map (app: lib.optionalString (benchDir != null) "${benchDir}/" + "apps/${app}") (
      lib.filter (app: app != devApp) appNames
    );

  # pyrightconfig.json: read by Pylance, pyright and basedpyright, and it
  # wins over their editor settings. Type checking is off, as it is in
  # Pylance by default: Frappe's own code is not typed, and the checkers the
  # app standards run are ruff and ty. Navigation, completion, hover and
  # unresolved imports keep working. `settings` (frappe-nix.ide.pyrightSettings)
  # goes over the top, key by key.
  pyrightConfig =
    {
      appNames,
      pythonVersion,
      benchDir ? null,
      devApp ? null,
      settings ? { },
    }:
    lib.recursiveUpdate {
      venvPath = ".";
      venv = ".venv";
      inherit pythonVersion;
      extraPaths = extraPaths { inherit appNames benchDir devApp; };
      typeCheckingMode = "off";
      # Pyright's own defaults (an `exclude` replaces them), plus the bench's
      # runtime trees: sites/assets links every app's public/ back in, and env
      # is the virtualenv, which `venv` already names.
      exclude = [
        "**/node_modules"
        "**/__pycache__"
        "**/.*"
        "env"
        "sites"
        "logs"
      ];
    } settings;

  # .vscode/settings.json, seeded once. The analysis roots are not here but in
  # pyrightconfig.json, which frappe-nix keeps current on every shell entry:
  # this file is the developer's after the first one.
  vscodeSettings = {
    "python.defaultInterpreterPath" = "\${workspaceFolder}/.venv/bin/python";
    "files.watcherExclude" = {
      "**/.devenv/**" = true;
      "**/.direnv/**" = true;
      "**/.frappe-nix/**" = true;
      "**/node_modules/**" = true;
    };
    "search.exclude" = {
      "**/.devenv/**" = true;
      "**/.direnv/**" = true;
    };
  };

  # .vscode/extensions.json, seeded once: the Python extension (which brings
  # Pylance) and ruff's.
  vscodeExtensions = {
    recommendations = [
      "ms-python.python"
      "charliermarsh.ruff"
    ];
  };
}
