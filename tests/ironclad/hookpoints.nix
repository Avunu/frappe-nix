# N3a's checks (docs/ironclad/spec.md §7, "N3a: hook points"): the seams the
# other Ironclad PRs build on.
#
#   ironclad-cli       the package builds against the locked nixpkgs with its
#                      unittest suites and pythonRuntimeDepsCheck; `--version`,
#                      the unknown-command exit and `data-path` behave.
#   ironclad-loaders   a lib/scripts.d file and a lib/ironclad/tools file are
#                      picked up (fixtures under ./fixtures) and a drop-in gets
#                      every argument and snippet; a drop-in that redefines a
#                      script, a tool named like an existing output and a
#                      check area that could shadow another check all fail
#                      evaluation.
#   ironclad-app-flake the fixture app's flake, evaluated against this checkout
#                      (what `--override-input frappe-nix path:.` does), exposes
#                      apps.frappe-init and the tools; the app-mode shell
#                      fragment carries `ironclad` and `frappe-init`, and none
#                      of its packages propagates anything, so no Python
#                      site-packages reach the shell's PYTHONPATH ahead of the
#                      bench venv's.
#
# The loader and flake facts are evaluated, so a regression fails
# `nix flake check --no-build` already; the derivations record what was seen.
{
  pkgs,
  lib,
  self,
  inputs,
  ironclad,
  ...
}:

let
  inherit (pkgs.stdenv.hostPlatform) system;

  version = lib.removeSuffix "\n" (builtins.readFile ../../version.txt);

  # --- loaders ---------------------------------------------------------------

  scriptsWith =
    appMode: scriptsDir:
    import ../../lib/scripts.nix {
      inherit
        lib
        pkgs
        appMode
        scriptsDir
        ;
      appsWithNode = [ ];
    };
  appScripts = scriptsWith true ./fixtures/scripts.d;
  benchScripts = scriptsWith false ./fixtures/scripts.d;
  clash = builtins.tryEval (builtins.attrNames (scriptsWith true ./fixtures/scripts.d-clash));
  # Every lib/scripts.nix argument and snippet (spec §1.4).
  missingDropInArgs = lib.subtractLists (lib.splitString " " appScripts.ironclad-fixture-args.exec) [
    "lib"
    "pkgs"
    "appsWithNode"
    "benchBin"
    "secrets"
    "nodeModulesBin"
    "nodeVerifyBin"
    "pythonBin"
    "nodeLocksBin"
    "nodeNestedFrontendExcludes"
    "restore"
    "offlineMigrate"
    "appMode"
    "lockDir"
    "atBench"
    "atRepo"
    "siteFlag"
    "offlineMigrateEnv"
    "workspaceBin"
    "registerWorkspaceMember"
    "refreshNodeModules"
    "refreshNodeModulesSoft"
    "regenNodeLocks"
    "regenNodeLocksSoft"
    "syncRegistry"
  ];
  # The real lib/scripts.d, in both modes: evaluating it is the clash check.
  realScripts = map (appMode: builtins.attrNames (scriptsWith appMode ../../lib/scripts.d)) [
    true
    false
  ];

  fixtureTools = import ../../lib/ironclad/outputs.nix {
    inherit pkgs;
    toolsDir = ./fixtures/tools;
  };
  realTools = import ../../lib/ironclad/outputs.nix { inherit pkgs; };
  # A tool named like an output frappe-nix already has (frappe-init) is refused.
  toolClash = builtins.tryEval (
    builtins.attrNames
      (import ../../lib/ironclad/outputs.nix {
        inherit pkgs;
        toolsDir = ./fixtures/tools-clash;
      }).packages
  );
  # An area defining a check outside ironclad-<name>, or ironclad-all, is refused.
  areaRefused =
    areasDir:
    !(builtins.tryEval (
      builtins.attrNames (
        import ./default.nix {
          inherit pkgs self inputs;
          inherit areasDir;
        }
      )
    )).success;

  loaderFacts =
    assert lib.assertMsg (
      appScripts ? ironclad-fixture-script
    ) "scripts.d: the fixture script is missing in app mode";
    assert lib.assertMsg
      (lib.hasInfix "ironclad fixture script" appScripts.ironclad-fixture-script.exec)
      "scripts.d: the fixture script lost its body";
    assert lib.assertMsg (
      appScripts ? bench-update
    ) "scripts.d: merging dropped lib/scripts.nix's own scripts";
    assert lib.assertMsg (
      !(benchScripts ? ironclad-fixture-script)
    ) "scripts.d: an app-mode-only drop-in leaked into bench mode";
    assert lib.assertMsg (
      missingDropInArgs == [ ]
    ) "scripts.d: a drop-in is not given ${lib.concatStringsSep ", " missingDropInArgs}";
    assert lib.assertMsg (
      !clash.success
    ) "scripts.d: a drop-in redefining bench-update did not fail evaluation";
    assert lib.assertMsg (builtins.all (
      names: names != [ ]
    ) realScripts) "scripts.d: lib/scripts.d does not evaluate";
    assert lib.assertMsg (
      fixtureTools.packages ? ironclad-fixture-tool
    ) "tools: the fixture tool package is missing";
    assert lib.assertMsg (
      fixtureTools.apps.ironclad-fixture-tool.type == "app"
    ) "tools: the fixture tool app is missing";
    assert lib.assertMsg
      (lib.hasSuffix "/bin/ironclad-fixture-tool" fixtureTools.apps.ironclad-fixture-tool.program)
      "tools: the fixture app's program is wrong";
    assert lib.assertMsg (
      realTools.packages ? ironclad && self.packages.${system} ? ironclad
    ) "tools: packages.ironclad is missing";
    assert lib.assertMsg (!toolClash.success) "tools: a tool named frappe-init did not fail evaluation";
    assert lib.assertMsg (areaRefused ./fixtures/areas-unprefixed)
      "checks: an area defining `ty` did not fail evaluation";
    assert lib.assertMsg (areaRefused ./fixtures/areas-all)
      "checks: an area defining ironclad-all did not fail evaluation";
    assert lib.assertMsg (self.apps.${system}.ironclad.type == "app") "tools: apps.ironclad is missing";
    {
      scriptsD = builtins.attrNames (removeAttrs appScripts (builtins.attrNames (scriptsWith true null)));
      tools = builtins.attrNames fixtureTools.packages;
      realTools = builtins.attrNames realTools.packages;
    };

  # --- the fixture app's flake against this frappe-nix ------------------------

  fixtureDir = ../fixtures/ironclad-app;
  fixtureInputs = {
    self = fixtureSelf;
    frappe-nix = self;
    inherit (inputs) nixpkgs;
    # A stand-in tree: nothing evaluated here reads frappe's sources.
    frappe = ../fixtures/app-workspace/frappe;
  };
  fixtureOutputs = (import (fixtureDir + "/flake.nix")).outputs fixtureInputs;
  fixtureSelf = fixtureOutputs // {
    _type = "flake";
    outPath = fixtureDir;
    inputs = fixtureInputs;
    sourceInfo.outPath = fixtureDir;
  };
  fixtureApps = fixtureOutputs.apps.${system};

  shellFragment = import ../../lib/ironclad/shell.nix { inherit pkgs; };
  shellPrograms = map lib.getExe shellFragment.packages;
  # A package that propagates inputs (a buildPythonPackage propagates python3
  # and its dependencies) drags them into the app's dev shell, where python's
  # setup hook puts their site-packages on PYTHONPATH.
  propagating = map (p: p.name) (
    builtins.filter (
      p:
      p ? pythonModule
      || (p.propagatedBuildInputs or [ ]) != [ ]
      || (p.propagatedNativeBuildInputs or [ ]) != [ ]
    ) shellFragment.packages
  );

  flakeFacts =
    assert lib.assertMsg (fixtureApps ? frappe-init) "app flake: apps.frappe-init is missing";
    assert lib.assertMsg (lib.hasSuffix "/bin/frappe-init" fixtureApps.frappe-init.program)
      "app flake: apps.frappe-init does not run frappe-init";
    assert lib.assertMsg (fixtureApps ? ironclad) "app flake: apps.ironclad is missing";
    assert lib.assertMsg (fixtureApps ? relock) "app flake: apps.relock went missing";
    assert lib.assertMsg (builtins.any (lib.hasSuffix "/bin/ironclad") shellPrograms)
      "app shell: ironclad is not among the packages";
    assert lib.assertMsg (builtins.any (lib.hasSuffix "/bin/frappe-init") shellPrograms)
      "app shell: frappe-init is not among the packages";
    assert lib.assertMsg (
      propagating == [ ]
    ) "app shell: ${lib.concatStringsSep ", " propagating} propagates inputs into the dev shell";
    assert lib.assertMsg (lib.hasInfix "blame.ignoreRevsFile" shellFragment.enterShell)
      "app shell: enterShell lost the blame setting";
    {
      apps = builtins.attrNames fixtureApps;
      shell = map (p: p.name) shellFragment.packages;
    };
in
{
  ironclad-cli = pkgs.runCommand "ironclad-cli-check" { nativeBuildInputs = [ ironclad ]; } ''
    set -euo pipefail
    fail() { echo "FAIL $*" >&2; exit 1; }

    got="$(ironclad --version)"
    [ "$got" = ${lib.escapeShellArg version} ] || fail "ironclad --version printed '$got', version.txt says ${version}"
    [ ${lib.escapeShellArg ironclad.version} = ${lib.escapeShellArg version} ] || fail "py/ironclad/pyproject.toml is not at version.txt's ${version}"
    echo "ok   ironclad --version = $got"

    set +e
    ironclad nonexistent 2> err
    code=$?
    set -e
    [ "$code" = 2 ] || fail "ironclad nonexistent exited $code, not 2"
    for c in config data-path pin-path; do
      grep -qw -- "$c" err || fail "ironclad nonexistent does not list $c: $(cat err)"
    done
    echo "ok   ironclad nonexistent exits 2 and lists the commands"

    for rel in known-apps.json schema/tool-ironclad.schema.json; do
      p="$(ironclad data-path "$rel")"
      [ -f "$p" ] || fail "data-path $rel printed '$p', which is not a file"
      echo "ok   data-path $rel"
    done

    touch "$out"
  '';

  ironclad-loaders = pkgs.runCommand "ironclad-loaders-check" { } ''
    set -euo pipefail
    [ "$(${fixtureTools.apps.ironclad-fixture-tool.program})" = ${lib.escapeShellArg version} ]
    cat > "$out" <<'EOF'
    ${builtins.toJSON loaderFacts}
    EOF
  '';

  # The shell fragment's packages as a build's inputs set up the environment
  # the dev shell gets: any propagated Python would show in PYTHONPATH.
  ironclad-app-flake =
    pkgs.runCommand "ironclad-app-flake-check"
      {
        nativeBuildInputs = shellFragment.packages;
        facts = builtins.toJSON flakeFacts;
      }
      ''
        set -euo pipefail
        if [ -n "''${PYTHONPATH:-}" ]; then
          echo "FAIL the app shell's packages put $PYTHONPATH on PYTHONPATH" >&2
          exit 1
        fi
        for f in ${
          lib.concatMapStringsSep " " (p: "${p}/nix-support/propagated-*") shellFragment.packages
        }; do
          if [ -e "$f" ]; then
            echo "FAIL $f: the app shell's packages propagate inputs" >&2
            exit 1
          fi
        done
        ironclad --version > /dev/null
        printf '%s\n' "$facts" > "$out"
      '';
}
