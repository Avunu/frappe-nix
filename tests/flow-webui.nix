# Checks for what lib/python.nix does for a Frappe bench's flow
# harness (`frappe-flow`, installed from `flow/harness` inside frappe's own
# directory), none of which the dev/ environment exercises: its Frappe predates
# the harness.
#
#   - the development set builds it: hatchling's editable build imports
#     `editables`, which nothing in uv.lock or the package names;
#   - the production set builds it without npm, from a web UI built offline on
#     its own (lib/flow-web-ui.nix), and the UI is in the result — the fixture's
#     hook fails the build where the real one would have run `npm ci`;
#   - in app mode, where `apps/frappe` is a mirror of an input, the harness is
#     taken from that input too, and the input may be a string that refers to
#     its store path (a flake input's outPath) as much as a path;
#   - Node and npm are in the build of what is built from source, and the
#     development set does not build a web UI.
#
# tests/fixtures/flow-webui is the bench; tests/fixtures/flow-webui-input holds
# the harness the way an app-mode input does. The bench itself carries only the
# harness's metadata, so a harness it does not recognise stays untouched, and
# the one place the full tree is reached is through `srcOverrides`.
{
  pkgs,
  checks,
  python-devops-nix,
  inputs,
}:

let
  inherit (pkgs) lib;

  envsOf =
    srcOverrides:
    import ../lib/python.nix {
      inherit
        pkgs
        lib
        python-devops-nix
        srcOverrides
        ;
      python = pkgs.python314;
      workspaceRoot = ./fixtures/flow-webui;
      name = "flow-webui";
      inherit (inputs) pyproject-nix pyproject-build-systems uv2nix;
    };

  # App mode: apps/frappe is only what uv2nix and the lock audit read.
  appMode = envsOf { frappe = ./fixtures/flow-webui-input; };
  # The same, the way a flake input arrives: a store path string with context.
  # Made from this flake's own source, not `"${./fixtures/…}"`: that copies the
  # fixture to a store path of its own, which `nix flake check --no-build`
  # (read-only) never writes, and the evaluation stops on "is not a valid
  # store path".
  appModeInput = envsOf { frappe = "${inputs.self}/tests/fixtures/flow-webui-input"; };
  # A bench whose harness is not one lib/flow-web-ui.nix knows.
  unknown = envsOf { };

  inputsOf = pkg: map (p: p.pname or p.name or "") pkg.nativeBuildInputs;
  has = name: pkg: lib.boolToString (lib.any (n: lib.hasPrefix name n) (inputsOf pkg));

  prod = appMode.prodPythonSet.frappe-flow;
  prodInput = appModeInput.prodPythonSet.frappe-flow;
  dev = appMode.editablePythonSet.frappe-flow;
in
{
  flow-webui = checks.script "flow-webui" { } ''
    fails=0
    ok()  { printf '  \033[32m✓\033[0m %s\n' "$1"; }
    no()  { printf '  \033[31m✗\033[0m %s\n' "$1"; fails=$((fails + 1)); }
    eq()  { if [ "$2" = "$3" ]; then ok "$1"; else no "$1"$'\n'"      expected: $2"$'\n'"      got:      $3"; fi; }

    # The production build: the hook ran as a source distribution would have it.
    site=$(echo ${prod}/lib/python*/site-packages)
    eq "the production build carries the web UI" \
      "yes" "$([ -f "$site/flow_harness/server/webui/index.html" ] && echo yes || echo no)"
    eq "…built from the UI's workspace, member links and all" \
      "yes" "$(grep -q 'built from the workspace member' "$site/flow_harness/server/webui/index.html" && echo yes || echo no)"
    eq "the web UI is built on its own, from the UI's tree" \
      "true" ${lib.escapeShellArg (lib.boolToString (prod ? webui))}
    eq "…and from an input given as a store path string" \
      "yes" "$([ -f "$(echo ${prodInput}/lib/python*/site-packages)/flow_harness/server/webui/index.html" ] && echo yes || echo no)"

    # The development build: editables is there, and nothing builds a UI.
    eq "the development build has editables" "true" ${has "editables" dev}
    eq "…and builds no web UI" "false" ${lib.escapeShellArg (lib.boolToString (dev ? webui))}
    eq "the editable install is a .pth file, not a copy" \
      "yes" "$(ls ${dev}/lib/python*/site-packages/*.pth >/dev/null 2>&1 && echo yes || echo no)"

    # Node and npm, as a fallback, in what is built from source.
    eq "the production build has Node and npm" "true" ${has "nodejs" prod}
    eq "…and the development build does not" "false" ${has "nodejs" dev}

    # A harness the lib does not know is left as the lock has it.
    eq "a harness it does not recognise gets no web UI" \
      "false" ${lib.escapeShellArg (lib.boolToString (unknown.prodPythonSet.frappe-flow ? webui))}

    echo ""
    if [ "$fails" -eq 0 ]; then
      echo "All flow-webui checks passed." | tee "$out"
    else
      echo "$fails check(s) failed."
      exit 1
    fi
  '';
}
