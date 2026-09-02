# Checks for lib/app-workspace.nix — the bench workspace app mode assembles in
# the store around a single Frappe app.
#
# What is actually at risk here is the generated root pyproject.toml. Three of
# its properties are load-bearing and all three fail *silently* when wrong:
#
#   [tool.uv.sources] keyed on an app's directory name rather than the
#   [project].name it declares is inert, and uv resolves that member from PyPI
#   instead — so the print_designer fixture declares "print-designer" on purpose;
#
#   [tool.uv].package must stay false, or uv tries to build the workspace root
#   itself and lib/python.nix's filter stops removing it from the venv;
#
#   sites/apps.txt is the order provision-site installs in, and frappe has to
#   be first.
{ pkgs }:

let
  inherit (pkgs) lib;

  fixtures = ./fixtures/app-workspace;

  workspace = import ../lib/app-workspace.nix {
    inherit pkgs lib;
    # Declaration order, deliberately not alphabetical: it is what apps.txt gets.
    apps = [
      {
        name = "frappe";
        src = fixtures + "/frappe";
      }
      {
        name = "print_designer";
        src = fixtures + "/print_designer";
      }
      {
        name = "legacy";
        src = fixtures + "/legacy";
      }
      {
        name = "carbon_frappe";
        src = fixtures + "/carbon_frappe";
      }
    ];
    projectName = "carbon-frappe-bench";
    preset = {
      python = "python314";
      nodejs = "nodejs_24";
      requiresPython = ">=3.14";
      overrideDependencies = [
        "click~=8.3.1"
        "requests>=2.34.2"
      ];
    };
  };
in
{
  app-workspace = pkgs.runCommand "frappe-nix-app-workspace-check" {
    nativeBuildInputs = [ pkgs.python3 ];
  } ''
    ws=${workspace}
    python3 - "$ws" <<'PY' | tee "$out"
    import sys, tomllib
    from pathlib import Path

    ws = Path(sys.argv[1])
    doc = tomllib.loads((ws / "pyproject.toml").read_text())
    fail = []

    def check(label, got, want):
        if got == want:
            print(f"  ok   {label}")
        else:
            print(f"  FAIL {label}\n       expected {want!r}\n       got      {got!r}")
            fail.append(label)

    check("root [project].name does not collide with a member",
          doc["project"]["name"], "carbon-frappe-bench")
    check("requires-python comes from the preset",
          doc["project"]["requires-python"], ">=3.14")
    check("the root stays a virtual package",
          doc["tool"]["uv"].get("package"), False)
    check("override-dependencies comes from the preset",
          doc["tool"]["uv"]["override-dependencies"],
          ["click~=8.3.1", "requests>=2.34.2"])

    # legacy/ has only a setup.py, so it is not a member — but it is still an
    # app, and apps.txt below must still list it.
    check("members are the apps with a pyproject.toml, in declaration order",
          doc["tool"]["uv"]["workspace"]["members"],
          ["apps/frappe", "apps/print_designer", "apps/carbon_frappe"])

    # The keys uv resolves on: each app's own [project].name, verbatim — uv
    # normalizes them itself. The print_designer fixture declares
    # "print-designer", so a directory-named key would show up here and the
    # member would silently come from PyPI instead.
    check("[tool.uv.sources] is keyed on each app's declared [project].name",
          sorted(doc["tool"]["uv"]["sources"]),
          ["carbon_frappe", "frappe", "print-designer"])
    check("every source resolves to the workspace",
          all(v == {"workspace": True} for v in doc["tool"]["uv"]["sources"].values()), True)

    # The build-time deps table the template carries for apps that predate
    # PEP 517. Losing it is a build failure several layers down, so assert it
    # survived substitution rather than trusting the template.
    check("extra-build-dependencies survived the render",
          "cairocffi" in doc["tool"]["uv"]["extra-build-dependencies"], True)

    check("no placeholder outlived substitution",
          "@" in (ws / "pyproject.toml").read_text().replace("@example.com", ""), False)

    apps_txt = (ws / "sites" / "apps.txt").read_text().split()
    check("apps.txt puts frappe first and keeps declaration order",
          apps_txt, ["frappe", "print_designer", "legacy", "carbon_frappe"])

    # Mirrored, not copied: the derivation is forced at evaluation time and
    # rebuilt whenever the app's source moves, so a real copy of the framework
    # would be re-hashed on every commit. But the directories themselves must be
    # real — uv2nix builds each member from its directory, and stdenv cannot
    # unpack a symlink.
    check("app directories are real",
          all((ws / "apps" / a).is_dir() and not (ws / "apps" / a).is_symlink()
              for a in ("frappe", "print_designer", "legacy", "carbon_frappe")), True)
    check("their files are symlinks back into the sources",
          (ws / "apps" / "frappe" / "pyproject.toml").is_symlink(), True)
    check("no uv.lock without one to copy", (ws / "uv.lock").exists(), False)

    if fail:
        print(f"\n{len(fail)} check(s) failed", file=sys.stderr)
        sys.exit(1)
    print("\nall checks passed")
    PY
  '';
}
