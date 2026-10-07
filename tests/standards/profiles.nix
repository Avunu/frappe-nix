# The built-in profiles (docs/app-standards/spec.md §8.5, S42, §7 N3a).
#
#   standards-profiles  data/profiles/minimal.toml and recommended@1.0.toml are
#                       byte-identical to the two TOML blocks of the committed
#                       spec's §8.5, apart from each file's leading comment
#                       header; both validate against the profile schema (the
#                       package's own suite, test_profiles.py, checks that and
#                       that every module has a table).
#
# From N6's first release on, a further check compares every released
# recommended@<minor> snapshot with its content at the tag that introduced it.
{
  pkgs,
  lib,
  ...
}:

let
  root = ../..;
  inputs = lib.fileset.toSource {
    inherit root;
    fileset = lib.fileset.unions [
      (root + "/docs/app-standards/spec.md")
      (root + "/py/frappe_nix_tools/frappe_nix_tools/data/profiles")
    ];
  };
  compare = pkgs.writeText "compare-profiles.py" ''
    import sys
    from pathlib import Path

    root = Path(sys.argv[1])
    spec = (root / "docs/app-standards/spec.md").read_text()
    profiles = root / "py/frappe_nix_tools/frappe_nix_tools/data/profiles"


    def block(after):
        start = spec.index("```toml\n", spec.index(after)) + len("```toml\n")
        return spec[start : spec.index("```\n", start)]


    def body(path):
        lines = path.read_text().splitlines(keepends=True)
        while lines and (lines[0].startswith("#") or not lines[0].strip()):
            lines.pop(0)
        return "".join(lines)


    failed = False
    for name, after in (
        ("minimal.toml", "`minimal.toml`, the dev shell and nothing else:"),
        ("recommended@1.0.toml", "`recommended@1.0.toml`, vendor-neutral defaults:"),
    ):
        if body(profiles / name) != block(after):
            print(f"FAIL {name} differs from the spec's §8.5 block", file=sys.stderr)
            failed = True
        else:
            print(f"ok   {name} is the spec's §8.5 block")
    sys.exit(1 if failed else 0)
  '';
in
{
  standards-profiles = pkgs.runCommand "standards-profiles-check" { } ''
    ${pkgs.python3}/bin/python3 ${compare} ${inputs}
    touch "$out"
  '';
}
