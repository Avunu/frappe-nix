# N3a's checks (docs/app-standards/spec.md §7, "N3a: hook points"): the seams
# the other app standards PRs build on.
#
#   standards-cli       the package builds against the locked nixpkgs with its
#                       unittest suites and pythonRuntimeDepsCheck; `--version`,
#                       the unknown-command exit, `data-path` and `config`
#                       behave.
#   standards-loaders   a lib/scripts.d file and a lib/standards/tools file are
#                       picked up (fixtures under ./fixtures) and a drop-in gets
#                       every argument and snippet; a drop-in that redefines a
#                       script, a tool named like an existing output and a
#                       check area that could shadow another check all fail
#                       evaluation.
#   standards-app-flake the fixture app's flake, evaluated against this checkout
#                       (what `--override-input frappe-nix path:.` does),
#                       exposes apps.frappe-init and the tools; the opted-in
#                       app-mode shell fragment carries `frappe-nix` and
#                       `frappe-init`, and none of its packages propagates
#                       anything, so no Python site-packages reach the shell's
#                       PYTHONPATH ahead of the bench venv's.
#   standards-optin     the opt-in test of lib/standards/shell.nix (S35): an app
#                       without [tool.frappe-nix] gets no package, no app and no
#                       enterShell; one with it gets them; a pyproject.toml that
#                       Nix's fromTOML rejects evaluates as not opted in (and as
#                       opted in with the table); a commented-out table line
#                       does not opt in.
#
# The loader and flake facts are evaluated, so a regression fails
# `nix flake check --no-build` already; the derivations record what was seen.
{
  pkgs,
  lib,
  self,
  inputs,
  frappeNixTools,
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
  # Every lib/scripts.nix argument and snippet (spec §1.2).
  missingDropInArgs = lib.subtractLists (lib.splitString " " appScripts.standards-fixture-args.exec) [
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

  fixtureTools = import ../../lib/standards/outputs.nix {
    inherit pkgs;
    toolsDir = ./fixtures/tools;
  };
  realTools = import ../../lib/standards/outputs.nix { inherit pkgs; };
  # A tool named like an output frappe-nix already has (frappe-init) is refused.
  toolClash = builtins.tryEval (
    builtins.attrNames
      (import ../../lib/standards/outputs.nix {
        inherit pkgs;
        toolsDir = ./fixtures/tools-clash;
      }).packages
  );
  # An area defining a check outside standards-<name>, or standards-all, is refused.
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
      appScripts ? standards-fixture-script
    ) "scripts.d: the fixture script is missing in app mode";
    assert lib.assertMsg
      (lib.hasInfix "standards fixture script" appScripts.standards-fixture-script.exec)
      "scripts.d: the fixture script lost its body";
    assert lib.assertMsg (
      appScripts ? bench-update
    ) "scripts.d: merging dropped lib/scripts.nix's own scripts";
    assert lib.assertMsg (
      !(benchScripts ? standards-fixture-script)
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
      fixtureTools.packages ? standards-fixture-tool
    ) "tools: the fixture tool package is missing";
    assert lib.assertMsg (
      fixtureTools.apps.standards-fixture-tool.type == "app"
    ) "tools: the fixture tool app is missing";
    assert lib.assertMsg
      (lib.hasSuffix "/bin/standards-fixture-tool" fixtureTools.apps.standards-fixture-tool.program)
      "tools: the fixture app's program is wrong";
    assert lib.assertMsg (
      realTools.packages ? frappe-nix && self.packages.${system} ? frappe-nix
    ) "tools: packages.frappe-nix is missing";
    assert lib.assertMsg (
      self.packages.${system} ? frappe-nix-tools
    ) "tools: packages.frappe-nix-tools is missing";
    assert lib.assertMsg (!toolClash.success) "tools: a tool named frappe-init did not fail evaluation";
    assert lib.assertMsg (areaRefused ./fixtures/areas-unprefixed)
      "checks: an area defining `ty` did not fail evaluation";
    assert lib.assertMsg (areaRefused ./fixtures/areas-all)
      "checks: an area defining standards-all did not fail evaluation";
    assert lib.assertMsg (
      self.apps.${system}.frappe-nix.type == "app"
    ) "tools: apps.frappe-nix is missing";
    {
      scriptsD = builtins.attrNames (removeAttrs appScripts (builtins.attrNames (scriptsWith true null)));
      tools = builtins.attrNames fixtureTools.packages;
      realTools = builtins.attrNames realTools.packages;
    };

  # --- the fixture app's flake against this frappe-nix ------------------------

  fixtureDir = ../fixtures/standards-app;
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

  shellFragment = import ../../lib/standards/shell.nix {
    inherit pkgs;
    pyproject = fixtureDir + "/pyproject.toml";
  };
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
    assert lib.assertMsg (fixtureApps ? frappe-nix) "app flake: apps.frappe-nix is missing";
    assert lib.assertMsg (fixtureApps ? relock) "app flake: apps.relock went missing";
    assert lib.assertMsg shellFragment.optedIn "app shell: the fixture app is not opted in";
    assert lib.assertMsg (builtins.any (lib.hasSuffix "/bin/frappe-nix") shellPrograms)
      "app shell: frappe-nix is not among the packages";
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
  standards-cli =
    pkgs.runCommand "standards-cli-check"
      {
        nativeBuildInputs = [
          self.packages.${system}.frappe-nix
          pkgs.git
        ];
      }
      ''
        set -euo pipefail
        fail() { echo "FAIL $*" >&2; exit 1; }

        got="$(frappe-nix --version)"
        [ "$got" = ${lib.escapeShellArg version} ] || fail "frappe-nix --version printed '$got', version.txt says ${version}"
        [ ${lib.escapeShellArg frappeNixTools.version} = ${lib.escapeShellArg version} ] || fail "py/frappe_nix_tools/pyproject.toml is not at version.txt's ${version}"
        echo "ok   frappe-nix --version = $got"

        set +e
        frappe-nix nonexistent 2> err
        code=$?
        set -e
        [ "$code" = 2 ] || fail "frappe-nix nonexistent exited $code, not 2"
        for c in config data-path pin-path; do
          grep -q "'$c'" err || fail "frappe-nix nonexistent does not list $c: $(cat err)"
        done
        echo "ok   frappe-nix nonexistent exits 2 and lists the commands"

        for rel in known-apps.json schema/tool-frappe-nix.schema.json schema/profile.schema.json \
          profiles/minimal.toml profiles/recommended@1.0.toml; do
          p="$(frappe-nix data-path "$rel")"
          [ -f "$p" ] || fail "data-path $rel printed '$p', which is not a file"
          echo "ok   data-path $rel"
        done

        # The fixture app resolves: recommended, so ssort is off and ci on.
        cp -r ${fixtureDir} app
        chmod -R u+w app
        cd app
        export HOME="$PWD"
        [ "$(frappe-nix config modules.ssort)" = false ] || fail "config modules.ssort is not false"
        [ "$(frappe-nix config modules.ci)" = true ] || fail "config modules.ci is not true"
        [ "$(frappe-nix config profile.name)" = recommended@1.0 ] || fail "config profile.name is not recommended@1.0"
        echo "ok   frappe-nix config on the fixture app"

        touch "$out"
      '';

  standards-loaders = pkgs.runCommand "standards-loaders-check" { } ''
    set -euo pipefail
    [ "$(${fixtureTools.apps.standards-fixture-tool.program})" = ${lib.escapeShellArg version} ]
    cat > "$out" <<'EOF'
    ${builtins.toJSON loaderFacts}
    EOF
  '';

  # The shell fragment's packages as a build's inputs set up the environment
  # the dev shell gets: any propagated Python would show in PYTHONPATH.
  standards-app-flake =
    pkgs.runCommand "standards-app-flake-check"
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
        frappe-nix --version > /dev/null
        printf '%s\n' "$facts" > "$out"
      '';

  # The opt-in test (S35). Each fixture's shell fragment is evaluated, so a
  # regression fails evaluation; the derivation records what was seen.
  standards-optin =
    let
      shellFor =
        name:
        import ../../lib/standards/shell.nix {
          inherit pkgs;
          pyproject = ./fixtures/optin + "/${name}/pyproject.toml";
        };
      expected = {
        without-table = false;
        with-table = true;
        datetime = false;
        datetime-opted-in = true;
        commented = false;
        subtable = true;
      };
      seen = lib.mapAttrs (name: _: (shellFor name).optedIn) expected;
      wrong = lib.filterAttrs (name: want: seen.${name} != want) expected;
      off = shellFor "without-table";
      on = shellFor "with-table";
      offPrograms = map lib.getExe off.packages;
      onPrograms = map lib.getExe on.packages;
      none = import ../../lib/standards/shell.nix { inherit pkgs; };
      facts =
        assert lib.assertMsg (wrong == { }) "opt-in: wrong verdict for ${builtins.toJSON wrong}";
        assert lib.assertMsg (
          off.packages == [ ] && off.apps == { } && off.enterShell == ""
        ) "opt-in: an app without [tool.frappe-nix] gets packages, apps or enterShell";
        assert lib.assertMsg (
          !(builtins.any (
            p:
            builtins.any (n: lib.hasSuffix "/bin/${n}" p) [
              "frappe-nix"
              "nixfmt"
              "statix"
              "deadnix"
            ]
          ) offPrograms)
        ) "opt-in: a tool reaches a shell that did not opt in";
        assert lib.assertMsg (
          !(lib.hasInfix "blame.ignoreRevsFile" off.enterShell)
        ) "opt-in: blame.ignoreRevsFile is set without opt-in";
        assert lib.assertMsg (
          !none.optedIn && none.packages == [ ]
        ) "opt-in: no pyproject.toml must mean not opted in";
        assert lib.assertMsg (builtins.any (lib.hasSuffix "/bin/frappe-nix") onPrograms)
          "opt-in: an opted-in shell lacks frappe-nix";
        assert lib.assertMsg (builtins.any (lib.hasSuffix "/bin/frappe-init") onPrograms)
          "opt-in: an opted-in shell lacks frappe-init";
        assert lib.assertMsg (
          on.apps ? frappe-init && on.apps ? frappe-nix
        ) "opt-in: an opted-in flake lacks apps.frappe-init or apps.frappe-nix";
        assert lib.assertMsg (lib.hasInfix "blame.ignoreRevsFile" on.enterShell)
          "opt-in: an opted-in shell does not set blame.ignoreRevsFile";
        seen;
    in
    pkgs.writeText "standards-optin-check" (builtins.toJSON facts);
}
