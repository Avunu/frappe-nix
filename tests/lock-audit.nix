# Checks for lib/lock-audit.nix — the preflight that turns a stale uv.lock into
# a sentence rather than an "attribute 'x' missing" inside uv2nix's resolver.
#
# The audit is pure evaluation, so the answers are computed here at eval time and
# the derivation only has to compare them against what was expected.
{ pkgs }:

let
  inherit (pkgs) lib;

  audit = import ../lib/lock-audit.nix { inherit lib; };

  # Stands in for the resolved package set. Deliberately holds the *normalized*
  # names, so a fixture requirement only matches if the audit normalizes it too.
  resolved = [
    "frappe-bench"
    "setuptools"
    "ruff"
    "pytest"
    "requests"
    "zope-interface"
    "pillow-simd"
  ];

  result = audit {
    workspaceRoot = ./fixtures/lock-audit;
    rootPyproject = builtins.fromTOML (builtins.readFile ./fixtures/lock-audit/pyproject.toml);
    hasPackage = name: builtins.elem name resolved;
  };

  # A workspace whose every declaration resolves: the audit must stay silent.
  clean = audit {
    workspaceRoot = ./fixtures/lock-audit;
    rootPyproject = {
      project.dependencies = [ "requests" ];
      tool.uv.workspace.members = [ ];
    };
    hasPackage = name: builtins.elem name resolved;
  };

  found = map (d: "${d.where}\t${d.name}") result.missing;

  # Every gap, and only the gaps: the marker-gated and direct-URL requirements
  # stay out (they can legitimately sit outside a resolution), extras stay out
  # (nothing requests them), `apps/beta` has no pyproject.toml and `apps/gone`
  # no directory, and the `include-group` attrset is not a requirement string.
  expected = [
    "apps/alpha/pyproject.toml\tjson-repair"
    "vendor/legacy/pyproject.toml\tvendor-gap"
    "pyproject.toml\troot-only-gap"
  ];
in
{
  lock-audit = pkgs.runCommand "frappe-nix-lock-audit-check" { } ''
    fails=0
    ok()  { printf '  \033[32m✓\033[0m %s\n' "$1"; }
    no()  { printf '  \033[31m✗\033[0m %s\n' "$1"; fails=$((fails + 1)); }
    eq()  { if [ "$2" = "$3" ]; then ok "$1"; else no "$1"$'\n'"      expected: $2"$'\n'"      got:      $3"; fi; }

    eq "every gap is reported, and only the gaps" \
      ${lib.escapeShellArg (lib.concatStringsSep "\n" expected)} \
      ${lib.escapeShellArg (lib.concatStringsSep "\n" found)}

    eq "a workspace with nothing missing reports nothing" \
      "" ${lib.escapeShellArg (lib.concatStringsSep "\n" (map (d: d.name) clean.missing))}

    ${lib.optionalString (result.missing != [ ]) ''
      eq "the message names the app, the requirement and the fix" \
        "yes" ${lib.escapeShellArg (
          if
            lib.hasInfix "apps/alpha/pyproject.toml requires json-repair>=0.30" result.message
            && lib.hasInfix "uv lock" result.message
            && lib.hasInfix "nix run .#relock" result.message
          then
            "yes"
          else
            result.message
        )}
    ''}

    echo ""
    if [ "$fails" -eq 0 ]; then
      echo "All lock-audit checks passed." | tee "$out"
    else
      echo "$fails check(s) failed."
      exit 1
    fi
  '';
}
