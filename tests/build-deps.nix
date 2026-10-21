# frappe-nix's use of python-devops-nix's lib.python.buildDeps (lib/build-deps.nix):
# the derivation itself is the base's, and tested there. What is frappe-nix's is
# its `always`, the template that seeds a bench with it, the after-lock `sync`
# that leaves a bench without the block alone, a bench's own entries, and
# ensure-root keeping out of the block.
{ pkgs, python-devops-nix }:

let
  inherit (pkgs) lib;

  tools = import ../lib/build-deps.nix { inherit pkgs python-devops-nix; };

  # A bench's own entry (frappe-nix.pythonBuildDependencies), for a package the
  # lock has no wheel for: it replaces the derived setuptools.
  withExtra = import ../lib/build-deps.nix {
    inherit pkgs python-devops-nix;
    extra.jsonata = [
      "setuptools"
      "pybind11"
    ];
  };

  workspaceTool = import ../lib/workspace-tool.nix { inherit pkgs; };

  # Two sdist-only packages, one with a wheel, frappe-runtime from git and an
  # editable member: what a bench's lock holds, cut down.
  lock = pkgs.writeText "uv.lock" ''
    version = 1
    requires-python = ">=3.14"

    [[package]]
    name = "docopt"
    version = "0.6.2"
    source = { registry = "https://pypi.org/simple" }
    sdist = { url = "https://example.invalid/docopt-0.6.2.tar.gz", hash = "sha256:00" }

    [[package]]
    name = "jsonata"
    version = "0.2.5"
    source = { registry = "https://pypi.org/simple" }
    sdist = { url = "https://example.invalid/jsonata-0.2.5.tar.gz", hash = "sha256:00" }

    [[package]]
    name = "requests"
    version = "2.32.0"
    source = { registry = "https://pypi.org/simple" }
    wheels = [{ url = "https://example.invalid/requests-2.32.0-py3-none-any.whl", hash = "sha256:00" }]

    [[package]]
    name = "frappe-runtime"
    version = "0.1.0"
    source = { git = "https://github.com/Avunu/frappe-nix?subdirectory=runtime#0000" }

    [[package]]
    name = "frappe"
    version = "16.0.0"
    source = { editable = "apps/frappe" }
  '';

  emptyLock = pkgs.writeText "uv.lock" ''
    version = 1
  '';

  legacy = pkgs.writeText "pyproject.toml" ''
    [project]
    name = "legacy-bench"
    version = "0.0.0"
    requires-python = ">=3.14"

    [tool.uv.extra-build-dependencies]
    "frappe-runtime" = ["hatchling"]
    "backcall" = ["setuptools"]
  '';

  expected = ''
    # >>> frappe-nix: sdist build deps (managed) >>>
    "cairocffi" = ["setuptools", "cffi", "pycparser"]
    "docopt" = ["setuptools"]
    "frappe-runtime" = ["hatchling"]
    "jsonata" = ["setuptools", "pybind11"]
    # <<< frappe-nix: sdist build deps (managed) <<<
  '';
in
{
  build-deps =
    pkgs.runCommand "frappe-nix-build-deps-check"
      {
        nativeBuildInputs = [
          pkgs.diffutils
          workspaceTool
        ];
      }
      ''
        set -euo pipefail
        fail() { echo "FAIL: $*" >&2; exit 1; }

        # The template's block is exactly what `update` writes for a lock with
        # nothing in it: frappe-nix's `always`, so a new bench starts current.
        ${lib.getExe tools.command} check ${../templates/bench/pyproject.toml} ${emptyLock} \
          || fail "templates/bench/pyproject.toml's block is not frappe-nix's always"
        echo "ok   the template's block is frappe-nix's always"

        # A bench from before the block: left as it is, with a hint, and the
        # caller's relock does not fail.
        cp ${legacy} pyproject.toml && chmod u+w pyproject.toml
        cp ${lock} uv.lock
        ${tools.sync}/bin/frappe-nix-build-deps-sync 2> hint
        cmp -s pyproject.toml ${legacy} || fail "sync rewrote a bench without the block"
        grep -q "hand-kept" hint || fail "sync gave no hint: $(cat hint)"
        echo "ok   a bench without the block keeps its list"

        # With the block: derived from the lock, the bench's own entry in place
        # of the derived one, the git source given hatchling, the wheel and the
        # editable member left out.
        { printf '[project]\nname = "b"\nversion = "0"\nrequires-python = ">=3.14"\n\n[tool.uv.extra-build-dependencies]\n'
          printf '# >>> frappe-nix: sdist build deps (managed) >>>\n# <<< frappe-nix: sdist build deps (managed) <<<\n'
        } > pyproject.toml
        ${withExtra.sync}/bin/frappe-nix-build-deps-sync
        sed -n '/^# >>>/,/^# <<</p' pyproject.toml > got
        printf '%s' ${lib.escapeShellArg expected} > want
        diff -u want got || fail "the derived block"
        ${lib.getExe withExtra.command} check || fail "check after update"
        echo "ok   the block is derived from uv.lock, with the bench's own entries"

        # ensure-root adds the template's entries to a bench that keeps its own
        # list, and nothing to one with the block: a key outside it would be
        # written a second time inside by the next update.
        sed -i '/"frappe-runtime"/d' pyproject.toml
        cp pyproject.toml before
        frappe-nix-workspace ensure-root --pyproject pyproject.toml --template ${../templates/bench/pyproject.toml} > /dev/null
        # The table alone, up to the next header (ensure-root may add other
        # tables after it, [tool.uv.sources]), blank lines left out.
        table() { awk '/^\[/ { p = ($0 == "[tool.uv.extra-build-dependencies]") } p && NF' "$1"; }
        table pyproject.toml > after-table
        table before > before-table
        diff -u before-table after-table || fail "ensure-root wrote into a bench with the block"
        cp ${legacy} pyproject.toml && chmod u+w pyproject.toml
        frappe-nix-workspace ensure-root --pyproject pyproject.toml --template ${../templates/bench/pyproject.toml} > /dev/null
        grep -q '^"\?cairocffi"\? = ' pyproject.toml || fail "ensure-root did not fill a hand-kept list"
        echo "ok   ensure-root fills a hand-kept list and leaves the block alone"

        touch "$out"
      '';
}
