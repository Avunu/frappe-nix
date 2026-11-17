# What `frappe-nix ci verify-relock` (py/frappe_nix_tools/frappe_nix_tools/ci/lockcheck.py)
# assumes about the build, checked against the build's own code.
#
#   standards-lockcheck-yarn  lockcheck's nix_yarn copies the line regexes of python-devops-nix
#                             lib/node/yarn-lock.nix (`parse`, what the offline mirror fetches),
#                             so that verify-relock refuses a yarn.lock its strict reading and
#                             the build read differently. Here `parse`, at the revision
#                             flake.lock pins, reads every lock of
#                             py/frappe_nix_tools/tests/yarn_corpus (test_lockcheck's corpus:
#                             what yarn writes, and the lines one reader takes and another
#                             skips) and every yarn.lock seed frappe-nix-tools ships, and
#                             nix_yarn must find the same entries, keys and fields in each. A
#                             python-devops-nix bump that changes how `parse` reads a lock
#                             fails here, where nothing else would.
{
  pkgs,
  lib,
  checks,
  inputs,
  frappeNixTools,
  ...
}:

let
  python = pkgs.python314.withPackages (_: [ frappeNixTools ]);
  yarnLock = inputs.python-devops-nix.lib.node.yarnLockWith { label = "standards-lockcheck-yarn"; };
  corpus = ../../py/frappe_nix_tools/tests/yarn_corpus;
  seeds = ../../py/frappe_nix_tools/frappe_nix_tools/data/node-locks;
  relative = root: path: lib.removePrefix "${toString root}/" (toString path);
  locks =
    map (case: {
      name = "corpus/${case}/yarn.lock";
      path = corpus + "/${case}/yarn.lock";
    }) (builtins.attrNames (lib.filterAttrs (_: type: type == "directory") (builtins.readDir corpus)))
    ++ map (path: {
      name = "seeds/${relative seeds path}";
      inherit path;
    }) (lib.filter (path: baseNameOf path == "yarn.lock") (lib.filesystem.listFilesRecursive seeds));
  files = pkgs.linkFarm "standards-lockcheck-yarn-locks" locks;
  parsed = pkgs.writeText "standards-lockcheck-yarn-parsed.json" (
    builtins.toJSON (
      map (lock: {
        inherit (lock) name;
        entries = yarnLock.parse lock.path;
      }) locks
    )
  );
in
{
  standards-lockcheck-yarn =
    checks.script "standards-lockcheck-yarn"
      {
        tools = [ python ];
      }
      ''
        set -euo pipefail
        python - ${parsed} ${files} <<'EOF'
        import json
        import sys
        from pathlib import Path

        from frappe_nix_tools.ci.lockcheck import nix_yarn

        parsed, files = json.loads(Path(sys.argv[1]).read_text()), Path(sys.argv[2])
        assert len([p for p in parsed if p["name"].startswith("corpus/")]) > 10, parsed
        assert any(p["name"].startswith("seeds/") for p in parsed), parsed
        bad = []
        for lock in parsed:
            text = (files / lock["name"]).read_bytes().decode("utf-8")
            ours = [{"key": key, **fields} for key, fields in nix_yarn(text)]
            if ours != lock["entries"]:
                bad.append(f"{lock['name']}:\n  parse:    {lock['entries']}\n  nix_yarn: {ours}")
        if bad:
            sys.exit("lib/node/yarn-lock.nix's parse and lockcheck's nix_yarn read these differently:\n" + "\n".join(bad))
        print(f"{len(parsed)} locks read the same by parse and nix_yarn")
        EOF
        touch "$out"
      '';
}
