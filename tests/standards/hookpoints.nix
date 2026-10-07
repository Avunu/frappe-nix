# N3a's checks (docs/app-standards/spec.md §7, "N3a: hook points"): the seams
# the other app standards PRs build on.
#
#   standards-cli       the package builds against the locked nixpkgs with its
#                       unittest suites and pythonRuntimeDepsCheck; `--version`,
#                       the unknown-command exit, `data-path` and `config`
#                       behave, and `config` reads the opt-in fixtures as
#                       standards-optin does or refuses them (exit 2).
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
#   standards-app-shell modules/devenv.nix's wiring of that fragment, read from
#                       the fixture app's own evaluation (opted in) and from
#                       the same flake over a source without [tool.frappe-nix]:
#                       the shell imports the fragment's devenv module, which
#                       carries frappe-nix, frappe-init and the blame setting
#                       only when opted in; the shell's other packages are the
#                       same either way and never include them; and the
#                       flake's apps differ by exactly the fragment's apps.
#   standards-optin     the opt-in test of lib/standards/shell.nix (S35): an app
#                       without [tool.frappe-nix] gets no package, no app and no
#                       enterShell; one with it gets them; a pyproject.toml that
#                       Nix's fromTOML rejects evaluates as not opted in (and as
#                       opted in with the table); a commented-out table line
#                       does not opt in. The spellings TOML and the line
#                       match disagree on (a quoted key, spaces in the
#                       brackets, dotted keys, a header line in a string) get
#                       the line match's verdict here; frappe-nix-tools
#                       refuses each with exit 2. CRLF line endings opt in;
#                       lone-CR ones don't, and TOML refuses them (exit 2).
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

  # --- the app-mode dev shell, as modules/devenv.nix wires it -----------------
  #
  # A whole devShell can't be evaluated here: it imports the generated bench
  # workspace (IFD) and needs the app's nix/uv.lock, which only a relock makes.
  # So the fixture flake is evaluated with flake-parts' `debug` on, and
  # modules/devenv.nix's own definition of `devenv.shells.default` is called
  # with the shell's config: its `imports` and `packages` are then plain
  # values. A package that needs the workspace throws, which tryEval skips.
  appShell =
    src:
    let
      debugFrappeNix = self // {
        lib = self.lib // {
          mkFlake =
            args: module:
            self.lib.mkFlake args {
              imports = [ module ];
              debug = true;
            };
        };
      };
      inputs' = fixtureInputs // {
        self = self';
        frappe-nix = debugFrappeNix;
      };
      outputs = (import (fixtureDir + "/flake.nix")).outputs inputs';
      self' = outputs // {
        _type = "flake";
        outPath = src;
        inputs = inputs';
        sourceInfo.outPath = src;
      };
      perSystem = outputs.allSystems.${system};
      ours = builtins.filter (
        d: lib.hasSuffix "/modules/devenv.nix" (lib.head (lib.splitString ", " d.file))
      ) perSystem.options.devenv.shells.definitionsWithLocations;
      shell = (lib.head ours).value.default {
        config = perSystem.devenv.shells.default;
        inherit lib pkgs;
      };
      key = (import ../../lib/standards/shell.nix { inherit pkgs; }).devenvModule.key;
      modules = builtins.filter (m: (m.key or null) == key) (shell.imports or [ ]);
      module = lib.head modules;
      names = map (p: p.name) module.packages;
      enter = module.enterShell;
    in
    assert lib.assertMsg (
      builtins.length ours == 1
    ) "app shell: modules/devenv.nix does not define devenv.shells.default once";
    {
      imported = builtins.length modules;
      packages = names;
      # Every other package's name, or null where it needs the bench workspace.
      others = map (
        p:
        let
          name = builtins.tryEval p.name;
        in
        if name.success then name.value else null
      ) shell.packages;
      # A plain definition is always on; a mkIf only when its condition holds.
      enterShellOn = enter._type or null != "if" || enter.condition;
      enterShell = if enter._type or null == "if" then enter.content else enter;
      apps = builtins.attrNames outputs.apps.${system};
    };

  shellOn = appShell fixtureDir;
  shellOff = appShell ./fixtures/optin/without-table;
  standardsNames = map (p: p.name) shellFragment.packages;
  standardsApps = builtins.attrNames shellFragment.apps;
  shellFacts =
    assert lib.assertMsg (
      shellOn.imported == 1 && shellOff.imported == 1
    ) "app shell: modules/devenv.nix does not import lib/standards/shell.nix's devenv module once";
    assert lib.assertMsg (standardsNames != [ ] && shellOn.packages == standardsNames)
      "app shell: an opted-in shell's standards packages are ${builtins.toJSON shellOn.packages}, not ${builtins.toJSON standardsNames}";
    assert lib.assertMsg (builtins.any (lib.hasPrefix "frappe-nix-") shellOn.packages)
      "app shell: an opted-in shell lacks frappe-nix";
    assert lib.assertMsg (builtins.elem "frappe-init" shellOn.packages)
      "app shell: an opted-in shell lacks frappe-init";
    assert lib.assertMsg (
      shellOn.enterShellOn && lib.hasInfix "blame.ignoreRevsFile" shellOn.enterShell
    ) "app shell: an opted-in shell does not set blame.ignoreRevsFile";
    assert lib.assertMsg (
      shellOff.packages == [ ]
    ) "app shell: a shell that did not opt in gets ${builtins.toJSON shellOff.packages}";
    assert lib.assertMsg (
      !shellOff.enterShellOn
    ) "app shell: a shell that did not opt in gets the standards enterShell";
    assert lib.assertMsg (
      shellOn.others == shellOff.others
    ) "app shell: opting in changes the shell's other packages";
    assert lib.assertMsg (
      !(builtins.any (n: builtins.elem n standardsNames) shellOff.others)
    ) "app shell: a standards tool is among a shell's own packages";
    assert lib.assertMsg (
      shellOff.apps == lib.subtractLists standardsApps shellOn.apps
      && lib.all (a: builtins.elem a shellOn.apps) standardsApps
    ) "app shell: the apps of a flake that did not opt in are ${builtins.toJSON shellOff.apps}";
    {
      on = removeAttrs shellOn [ "enterShell" ];
      off = removeAttrs shellOff [ "enterShell" ];
    };

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

  # --- opt-in ----------------------------------------------------------------

  # The opt-in fixtures' texts by name: each directory under ./fixtures/optin,
  # and with-table's text with CRLF and with lone-CR line endings. Those two
  # are made here (as strings, and as files in standards-cli) rather than
  # tracked: a lone CR is not TOML, so check-toml would refuse it. Nix splits
  # the raw text on \n, so CRLF lines still match the header while a lone-CR
  # file is a single line that doesn't.
  optinWithTable = builtins.readFile ./fixtures/optin/with-table/pyproject.toml;
  optinEndings = {
    crlf = builtins.replaceStrings [ "\n" ] [ "\r\n" ] optinWithTable;
    lone-cr = builtins.replaceStrings [ "\n" ] [ "\r" ] optinWithTable;
  };
  # `<name>=<file>` words for the standards-cli loops, which first write the
  # line-ending cases to ./endings.
  optinCases = lib.concatMapStringsSep " " (
    name:
    if optinEndings ? ${name} then
      "${name}=$PWD/endings/${name}.toml"
    else
      "${name}=${./fixtures/optin + "/${name}/pyproject.toml"}"
  );
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

        # The opt-in fixtures, read by the tools: they agree with the line match
        # (standards-optin), or refuse a spelling the two would disagree on.
        mkdir endings
        ${lib.concatStrings (
          lib.mapAttrsToList (name: text: ''
            printf '%s' ${lib.escapeShellArg text} > endings/${name}.toml
          '') optinEndings
        )}
        for pair in ${
          optinCases [
            "with-table"
            "datetime-opted-in"
            "crlf"
          ]
        }; do
          [ "$(frappe-nix config frappe-major --pyproject "''${pair#*=}")" = 16 ] \
            || fail "config does not read the table of optin/''${pair%%=*}"
        done
        for pair in ${
          optinCases [
            "without-table"
            "datetime"
            "commented"
            "quoted-key"
            "spaced-brackets"
            "dotted-keys"
            "in-string"
            "lone-cr"
          ]
        }; do
          set +e
          frappe-nix config frappe-major --pyproject "''${pair#*=}" 2> err
          code=$?
          set -e
          [ "$code" = 2 ] || fail "config on optin/''${pair%%=*} exited $code, not 2: $(cat err)"
        done
        echo "ok   frappe-nix config agrees with the shell's opt-in test or refuses the spelling"

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

  standards-app-shell = pkgs.writeText "standards-app-shell-check" (builtins.toJSON shellFacts);

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
        # Spellings TOML and the line match disagree on; frappe-nix-tools refuses
        # each of them (exit 2; py/frappe_nix_tools/tests/test_config.py).
        quoted-key = false;
        spaced-brackets = false;
        dotted-keys = false;
        in-string = true;
        # Line endings: CRLF lines still match; a lone-CR file is one line that
        # doesn't, and TOML refuses it (exit 2).
        crlf = true;
        lone-cr = false;
      };
      # The line-ending cases are strings, which the fragment's own optedInText
      # tests as it tests the file builtins.readFile returns.
      seen = lib.mapAttrs (
        name: _:
        if optinEndings ? ${name} then
          (shellFor "with-table").optedInText optinEndings.${name}
        else
          (shellFor name).optedIn
      ) expected;
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
