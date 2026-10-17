{
  description = "Reusable Nix infrastructure for Frappe bench projects";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-parts.url = "github:hercules-ci/flake-parts";
    devenv.url = "github:cachix/devenv";
    nix2container = {
      url = "github:nlewo/nix2container";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    pyproject-nix = {
      url = "github:pyproject-nix/pyproject.nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    uv2nix = {
      url = "github:pyproject-nix/uv2nix";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    pyproject-build-systems = {
      url = "github:pyproject-nix/build-system-pkgs";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.uv2nix.follows = "uv2nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    # Decrypts age secrets into a dev shell. Imported by
    # modules/flake-module.nix, so consumers do not declare it themselves.
    #
    # The agenix *CLI* is deliberately not an input: ryantm/agenix destructures
    # `darwin` and `home-manager` positionally in its outputs, so they cannot be
    # `follows = ""`-ed away and would land in every consumer's lock. `ragenix`
    # is in nixpkgs, is a drop-in (same RULES / -e / -r / -d / -i), and ships an
    # `agenix` symlink.
    agenix-shell = {
      url = "github:aciceri/agenix-shell";
      inputs.nixpkgs.follows = "nixpkgs";
      inputs.flake-parts.follows = "flake-parts";
    };

    # The pieces frappe-nix shares with odoo-nix: the secrets toolkit, the
    # backup fetch layer and the repository checks
    # (https://github.com/Avunu/python-devops-nix). Its own inputs are nixpkgs
    # and flake-parts, both followed, so it adds one node to the lock.
    python-devops-nix = {
      url = "github:Avunu/python-devops-nix";
      inputs.nixpkgs.follows = "nixpkgs";
      inputs.flake-parts.follows = "flake-parts";
    };

    # The registry checks an app must pass, pinned (docs/app-standards/spec.md S20).
    # Not flakes: plain source trees. They reach each app through its
    # flake.lock, where `frappe-nix pin-path <name>` finds them under the
    # frappe-nix node, and Dependabot's `nix` entry moves them here.
    #
    # frappe/marketplace: the registry's own semgrep rules and add_release.py.
    marketplace = {
      url = "github:frappe/marketplace";
      flake = false;
    };
    # frappe/pilot: the get-app validator (and app-assets.yml).
    pilot = {
      url = "github:frappe/pilot/develop";
      flake = false;
    };
    # frappe/semgrep-rules: the rules every app's `lint` runs.
    frappe-semgrep-rules = {
      url = "github:frappe/semgrep-rules";
      flake = false;
    };
  };

  nixConfig = {
    extra-substituters = [
      "https://devenv.cachix.org"
    ];
    extra-trusted-public-keys = [
      "devenv.cachix.org-1:w1cLUi8dv3hnoSPGAuibQv+f9TZLr6cv/Hm9XgU50cw="
    ];
  };

  outputs =
    {
      self,
      nixpkgs,
      flake-parts,
      ...
    }@inputs:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
        "x86_64-darwin"
        "aarch64-darwin"
      ];
      forAllSystems = f: nixpkgs.lib.genAttrs systems (system: f (import nixpkgs { inherit system; }));
      frappeInit =
        pkgs:
        import ./lib/init.nix {
          inherit pkgs;
          inherit (inputs) python-devops-nix;
        };
      # The app standards tools: packages.frappe-nix-tools, and one package and
      # app per lib/standards/tools/*.nix (`frappe-nix`, …).
      standards = pkgs: import ./lib/standards/outputs.nix { inherit pkgs; };
    in
    {
      flakeModules.default = ./modules/flake-module.nix;

      # importApply keeps the file's own path in the module system's errors.
      nixosModules.default = nixpkgs.lib.modules.importApply ./modules/nixos.nix {
        inherit (inputs.python-devops-nix.lib.python) threadCaps;
      };

      lib = {
        mkFlake =
          {
            inputs ? { },
            ...
          }:
          config:
          flake-parts.lib.mkFlake {
            inputs = self.inputs // inputs;
          } config;

        overrides = import ./lib/overrides.nix;
      };

      # `nix run github:Avunu/frappe-nix` scaffolds a new bench (bench-init style).
      packages = forAllSystems (
        pkgs:
        (standards pkgs).packages
        // rec {
          frappe-init = frappeInit pkgs;
          default = frappe-init;

          # The object-store half of `bench restore`, standalone. Exposed because
          # a production host restores too, and its NixOS module otherwise keeps
          # its own copy of the same folder-selection and download logic — which
          # is how the three copies of this got out of step in the first place.
          # `--fetch-only` style use: it prints a JSON manifest and touches no
          # database, so the deployment keeps its own restore half.
          backup-fetch = import ./lib/backup-fetch.nix {
            inherit pkgs;
            inherit (inputs) python-devops-nix;
          };
        }
      );

      apps = forAllSystems (
        pkgs:
        let
          program = "${frappeInit pkgs}/bin/frappe-init";
          app = {
            type = "app";
            inherit program;
            meta.description = "Scaffold a new frappe-nix bench (bench-init style)";
          };
        in
        (standards pkgs).apps
        // {
          default = app;
          frappe-init = app;
        }
      );

      checks = forAllSystems (
        pkgs:
        let
          grafts = import ./lib/grafts.nix {
            inherit pkgs;
            inherit (inputs) python-devops-nix;
          };
          # lib/<x> with frappe_<x> as lib/grafts.nix builds it (the shared
          # _hook.py merged in), so each suite tests the package that ships.
          graftTree = x: ''
            cp -r ${./lib + "/${x}"} ./${x}
            chmod -R u+w ./${x}
            rm -rf ./${x}/frappe_${x}
            cp -r --no-preserve=mode ${grafts.${x}}/frappe_${x} ./${x}/
          '';
        in
        {
          # The Python runtime under runtime/ builds as a distribution. Its unit
          # suite needs a bench (it imports frappe), so that runs from
          # runtime/scripts/run-tests.sh rather than here.
          runtime = pkgs.python3Packages.callPackage ./runtime/package.nix { };

          # Likewise Frappe-independent: stub modules stand in for frappe.app,
          # frappe.database and werkzeug.serving, and the assertions are about
          # which address the server was told to bind.
          unixsock = pkgs.runCommand "frappe-unixsock-check" { } ''
            ${graftTree "unixsock"}
            ${pkgs.python3}/bin/python ./unixsock/tests/test_unixsock.py | tee "$out"
          '';

          # Frappe- and systemd-independent: stub frappe.utils.logger and
          # bench.utils modules stand in, JOURNAL_STREAM is pointed at the test's
          # own stderr, and the assertions are about the <N> prefix on every line
          # and the bench.log that is never created. The runtime's copy of the
          # formatter is rendered against the graft's, so the two cannot drift.
          journald = pkgs.runCommand "frappe-journald-check" { } ''
            ${graftTree "journald"}
            ${pkgs.python3}/bin/python ./journald/tests/test_journald.py \
              ${./runtime/src/frappe_runtime/journald.py} | tee "$out"
          '';

          # Frappe-, MariaDB- and percona-independent: a stub frappe.local.db
          # stands in for the database and a shell script for
          # pt-online-schema-change, and the assertions are about what planning
          # may write (nothing), what is on the command line (no credential) and
          # what is left behind (no option file). lib/offline-migrate.py.
          offline-migrate = pkgs.runCommand "frappe-nix-offline-migrate-check" { } ''
            ${pkgs.python3}/bin/python ${./tests/test_offline_migrate.py} ${./lib/offline-migrate.py} | tee "$out"
          '';

          # Frappe- and node-independent: a stub frappe.build stands in, and the
          # assertions are about the NODE_OPTIONS frappe's build hands node.
          nodebuild = pkgs.runCommand "frappe-nodebuild-check" { } ''
            ${graftTree "nodebuild"}
            ${pkgs.python3}/bin/python ./nodebuild/tests/test_nodebuild.py | tee "$out"
          '';

          # Frappe- and bench-independent: a stub bench.cli and two stub scripts
          # stand in, and the assertions are about which command runs. Given
          # lib/scripts.nix, it also checks that the routes are the wrapper's.
          benchcli = pkgs.runCommand "frappe-benchcli-check" { } ''
            ${graftTree "benchcli"}
            ${pkgs.python3}/bin/python ./benchcli/tests/test_benchcli.py ${./lib/scripts.nix} | tee "$out"
          '';

          # Also Frappe-independent: a fixture stands in for the patch list
          # frappe-bench ships, and the assertions are about what the reconcile
          # leaves in the bench root's patches.txt.
          bench-patches =
            pkgs.runCommand "frappe-nix-bench-patches-check"
              {
                nativeBuildInputs = [ pkgs.findutils ];
              }
              ''
                bash ${./tests/bench-patches.sh} \
                  ${import ./lib/bench-patches.nix { inherit pkgs; }}/bin/frappe-nix-bench-patches \
                  2>&1 | tee "$out"
              '';

          # The datadir's btrfs NOCOW attribute: set on a fresh one, reported on
          # one already holding data, and the one-off copy that fixes that.
          # Branches on the sandbox's own filesystem, so it checks the real
          # attribute on a btrfs builder and the no-op path everywhere else.
          db-nocow =
            pkgs.runCommand "frappe-nix-db-nocow-check"
              {
                nativeBuildInputs = [
                  pkgs.e2fsprogs
                  pkgs.procps
                ];
              }
              ''
                bash ${./tests/db-nocow.sh} \
                  ${import ./lib/db-nocow.nix { inherit pkgs; }}/bin/frappe-nix-db-nocow \
                  2>&1 | tee "$out"
              '';

          # The dev shell's watch process: which apps the publisher rule
          # leaves out (fixture hooks.py files, no frappe), and that the
          # esbuild preload corrects frappe's builds and no one else's, and
          # skips the right-to-left build only where asked (a stand-in esbuild
          # with getter exports, like the real one).
          bench-watch =
            pkgs.runCommand "frappe-nix-bench-watch-check"
              {
                nativeBuildInputs = [
                  pkgs.python3
                  pkgs.nodejs
                ];
              }
              ''
                {
                  python3 ${./tests/bench-watch.py} ${./lib/bench-watch.py}
                  node ${./tests/esbuild-preload.js} ${./lib/js}/esbuild-preload.js
                } 2>&1 | tee "$out"
              '';

          # Native Sass for the watcher: the packaged sass-embedded finds
          # nixpkgs' compiler and serves the legacy render() frappe's postcss
          # plugin calls — JS importer and includedFiles included. Linux and
          # Darwin alike, wherever nixpkgs builds dart-sass.
          sass-embedded =
            let
              sassEmbedded = import ./lib/sass-embedded.nix { inherit pkgs; };
            in
            pkgs.runCommand "frappe-nix-sass-embedded-check" { nativeBuildInputs = [ pkgs.nodejs ]; } ''
              export HOME="$PWD"
              node ${./tests/sass-embedded.js} ${sassEmbedded}/${sassEmbedded.module} 2>&1 | tee "$out"
            '';

          # What shell entry does about apps/: checks out a fresh clone's apps
          # once, and past that touches nothing — entry once checked out any
          # submodule it found without a checkout, re-cloning apps that had
          # been removed.
          apps-report =
            pkgs.runCommand "frappe-nix-apps-report-check"
              {
                nativeBuildInputs = [
                  pkgs.git
                  pkgs.findutils
                ];
              }
              ''
                export HOME="$PWD"
                bash ${./tests/apps-report.sh} \
                  ${import ./lib/apps-report.nix { inherit pkgs; }}/bin/frappe-nix-apps-report \
                  2>&1 | tee "$out"
              '';

          # Node-independent in the same spirit: a stub yarn stands in for the
          # install, and the assertions are about when a pulled app is judged to
          # have outgrown its node_modules.
          node-modules =
            pkgs.runCommand "frappe-nix-node-modules-check"
              {
                nativeBuildInputs = [ pkgs.findutils ];
              }
              ''
                bash ${./tests/node-modules.sh} \
                  ${import ./lib/node-modules.nix { inherit pkgs; }}/bin/frappe-nix-node-modules \
                  2>&1 | tee "$out"
              '';

          # The shell-entry root sync over a bench from before frappe-runtime,
          # with a stub uv standing in for the lock; the assertions are about
          # what pyproject.toml gains, when the lock is regenerated, and that a
          # failed lock leaves both files exactly as they were.
          root-sync =
            pkgs.runCommand "frappe-nix-root-sync-check"
              {
                nativeBuildInputs = [ pkgs.python3 ];
              }
              ''
                bash ${./tests/root-sync.sh} \
                  ${import ./lib/root-sync.nix { inherit pkgs; }}/bin/frappe-nix-root-sync \
                  ${./templates/bench/pyproject.toml} \
                  2>&1 | tee "$out"
              '';

          # The fallback lock generator over the same fixture tree the Nix-side
          # discovery is checked against, with a stub yarn standing in for the
          # resolver; the assertions are about what lands in node-locks/, for
          # which targets, and when it is regenerated.
          node-locks =
            pkgs.runCommand "frappe-nix-node-locks-check"
              {
                nativeBuildInputs = [
                  pkgs.findutils
                  pkgs.jq
                ];
              }
              ''
                export HOME="$PWD"
                bash ${./tests/node-locks.sh} \
                  ${import ./lib/node-locks.nix { inherit pkgs; }}/bin/frappe-nix-node-locks \
                  ${./tests/fixtures/node-targets/apps} \
                  2>&1 | tee "$out"
              '';

          # The registry writer over a fixture bench: which apps land in
          # sites/apps.txt, and what apps.json records about each. Real git
          # repositories stand in for the checkouts, so the provenance
          # precedence is tested against git's answers rather than a stub's.
          apps-registry =
            pkgs.runCommand "frappe-nix-apps-registry-check"
              {
                nativeBuildInputs = [
                  pkgs.git
                  pkgs.jq
                  pkgs.python3
                ];
              }
              ''
                export HOME="$PWD"
                bash ${./tests/apps-registry.sh} \
                  ${import ./lib/workspace-tool.nix { inherit pkgs; }}/bin/frappe-nix-workspace \
                  2>&1 | tee "$out"
              '';

          # The other half of an exclusion: a nested frontend that is not
          # installed must also not be reachable from its parent app's build
          # script, or `bench build` fails on the missing binary instead of
          # just missing the frontend's routes.
          nested-frontend-scripts = pkgs.runCommand "frappe-nix-nested-frontend-scripts-check" { } ''
            bash ${./tests/nested-frontend-scripts.sh} \
              ${pkgs.nodejs}/bin/node \
              ${./lib/js/drop-nested-frontend-scripts.js} \
              2>&1 | tee "$out"
          '';
        }
        # frappe-nix.secrets as a bench declares it: the option tree, the
        # agenix-shell secret set, the rules and flake.lib.secrets that
        # python-devops-nix's module derives from frappe-nix's names and sites.
        # The toolkit's own tests (agecheck, edit-secret, rekey-secrets,
        # check-secrets, the backup fetch layer) are python-devops-nix's.
        // import ./tests/secrets-module.nix { inherit self pkgs; }
        # frappe_devguard: frappe-nix's guards against the package a bench
        # grafts, python-devops-nix's core suite on the presets' interpreters,
        # and its absence from the production virtualenv (design rule 7).
        // import ./tests/devguard.nix { inherit self pkgs inputs; }
        # Track B's renames: each old option name still reaches the new one,
        # and is listed for its deprecation warning.
        // import ./tests/renames.nix { inherit self pkgs inputs; }
        # Finding and repairing what yarn cannot see is broken, and the Python
        # half of `bench setup requirements`.
        // import ./tests/node-verify.nix { inherit pkgs; }
        // import ./tests/setup-requirements.nix { inherit pkgs; }
        # The real mariadbd, started through the dev shell's wrapper the way
        # devenv starts it: argument order, and where its temp files go.
        // import ./tests/mariadbd-wrapper.nix { inherit pkgs; }
        # The `bench restore` script itself, rendered and driven against a
        # fixture bucket and a stub bench.
        // import ./tests/bench-restore.nix {
          inherit pkgs;
          inherit (inputs) python-devops-nix;
        }
        # `bench-migrate` and `bench-offline-migrate`: the step in front of the
        # migrate, over stubs.
        // import ./tests/offline-migrate-scripts.nix { inherit pkgs; }
        # The `reconcile-apps` script (issue #32, part 1): sites/apps.txt vs.
        # a site's actually-installed apps, driven against a stub bench.
        // import ./tests/reconcile-apps.nix { inherit pkgs; }
        # The asset-shadow reassert check (issue #32, part 2): the
        # assets.json invariant and its reassert hooks, standalone.
        // import ./tests/assets-reassert.nix { inherit pkgs; }
        # `bench-update --pull` over a submodule, a local app and a stray repo.
        // import ./tests/bench-update.nix { inherit pkgs; }
        # `update-deps`: how it calls yarn in a bench (never writing a lock the
        # app's repository owns) and in app mode.
        // import ./tests/update-deps.nix { inherit pkgs; }
        # `bench-get-app` against file:// remotes: what it records.
        // import ./tests/bench-get-app.nix { inherit pkgs; }
        # `bench-remove-app`, its inverse: what it tears out — with a dirty
        # .gitmodules too — and what it refuses to touch.
        // import ./tests/bench-remove-app.nix { inherit pkgs; }
        # The stale-uv.lock preflight, over a fixture workspace.
        // import ./tests/lock-audit.nix {
          inherit pkgs;
          inherit (inputs) python-devops-nix;
        }
        # [tool.uv.extra-build-dependencies] from uv.lock: frappe-nix's always,
        # the template, the after-lock sync, a bench's own entries.
        // import ./tests/build-deps.nix {
          inherit pkgs;
          inherit (inputs) python-devops-nix;
        }
        # What an editable workspace member is built from in the dev shell.
        // import ./tests/editable-src.nix { inherit pkgs; }
        # Which apps/<x> and apps/<x>/<y> get a node lock, over a fixture tree.
        // import ./tests/node-targets.nix { inherit pkgs; }
        # yarn.lock → offline mirror: the parser, the naming, what is fetched.
        // import ./tests/yarn-lock.nix { inherit pkgs; }
        # Which lock each target builds from — its own, a fallback, a forced
        # fallback — and the notices that go with it.
        // import ./tests/node-locks-precedence.nix { inherit pkgs; }
        # The bench workspace app mode assembles around a single app.
        // import ./tests/app-workspace.nix { inherit pkgs; }
        # `frappe-init --migrate` over a synthetic classic bench. Offline, so it
        # is cheap enough to gate PRs on.
        // import ./tests/migrate-classic.nix {
          inherit pkgs;
          frappe-init = frappeInit pkgs;
        }
        # The journald fields and services.frappe-nix.logging, at evaluation. Linux
        # only because it evaluates a NixOS system, but no VM: cheap enough for
        # every PR.
        // nixpkgs.lib.optionalAttrs pkgs.stdenv.hostPlatform.isLinux (
          import ./tests/logging-fields.nix { inherit self pkgs; }
          # What services.frappe-nix.migrate.offline puts in the migrate unit, on and off.
          // import ./tests/offline-migrate-unit.nix { inherit self pkgs; }
          # What services.frappe-nix.chromium and .wkhtmltopdf put in the units and the
          # bench wrapper.
          // import ./tests/pdf-renderers-unit.nix { inherit self pkgs; }
          # The container image names, with and without containers.registry.
          // import ./tests/containers-registry.nix { inherit self pkgs; }
        )
        # NixOS VM tests (Linux only — runNixOSTest builds a VM).
        // nixpkgs.lib.optionalAttrs pkgs.stdenv.hostPlatform.isLinux {
          migrate-rollback = pkgs.testers.runNixOSTest (
            import ./tests/migrate-rollback.nix { inherit self pkgs; }
          );
          socket-runtime = pkgs.testers.runNixOSTest (
            import ./tests/socket-runtime.nix { inherit self pkgs; }
          );
          socket = pkgs.testers.runNixOSTest (import ./tests/socket.nix { inherit self pkgs; });
          pdf-renderers = pkgs.testers.runNixOSTest (import ./tests/pdf-renderers.nix { inherit self pkgs; });
        }
        # frappe-nix's own lint and type checks, and the drift check that keeps
        # its ruff config on the pinned upstream Frappe's. See dev/.
        // import ./dev/checks.nix { inherit pkgs inputs; }
        # The repository checks frappe-nix shares with odoo-nix
        # (python-devops-nix, CONVENTIONS.md, Formatter and Repository files):
        # nixfmt-rs, statix and deadnix over the tree with statix.toml compared
        # to the shared copy, and .editorconfig, .pre-commit-config.yaml and
        # ruff.toml carrying the shared settings.
        // {
          nix-lint = inputs.python-devops-nix.lib.checks.nixLint {
            inherit pkgs;
            src = ./.;
            product = "frappe-nix";
            # Fixtures stand in for third-party benches and templates hold
            # @PLACEHOLDERS@, so neither is Nix that parses; keep these in step
            # with `exclude` in .pre-commit-config.yaml.
            ignore = [
              "tests/fixtures"
              "templates"
            ];
          };
          shared-config = inputs.python-devops-nix.lib.checks.sharedConfig {
            inherit pkgs;
            src = ./.;
            product = "frappe-nix";
            # frappe-nix's Python is written in Frappe's style, as its
            # ruff.toml extends Frappe's own config and its code is read and
            # copied next to Frappe's: tabs, where the shared copy (vendored
            # into both products, linted by neither) uses Ruff's four spaces.
            exceptions.".editorconfig" = [
              [
                "*.py"
                "indent_style"
              ]
            ];
          };
        }
        # The app standards checks, one file per area, and `standards-all`
        # that builds them all. See tests/standards/default.nix.
        // import ./tests/standards { inherit self pkgs inputs; }
      );

      # frappe-nix's own dev shell: what a bench gets, pointed back at this
      # repository. `.envrc` enters it; see dev/devenv.nix.
      devShells = forAllSystems (pkgs: {
        default = inputs.devenv.lib.mkShell {
          inherit inputs pkgs;
          modules = [ ./dev/devenv.nix ];
        };
      });

      # Configure the formatter for nix fmt
      formatter = forAllSystems (
        pkgs:
        pkgs.writeShellScriptBin "nixfmt" ''
          # Files or flags passed by the nix driver: forward them directly
          [ "$#" -gt 0 ] && exec ${pkgs.lib.getExe pkgs.nixfmt-rs} "$@"
          # Bare 'nix fmt': skip templates/, whose @PLACEHOLDER@ files don't parse
          exec ${pkgs.findutils}/bin/find . \
            \( -path ./templates -o -path ./.git -o -path ./.direnv \) -prune -o \
            -name '*.nix' -exec ${pkgs.lib.getExe pkgs.nixfmt-rs} {} +
        ''
      );
    };
}
