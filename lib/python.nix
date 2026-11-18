# Python environment factory for Frappe bench projects.
#
# Builds production and development Python environments from a uv workspace,
# handling the common pattern of filtering out the root virtual package and
# re-adding its direct dependencies.
#
# Usage:
#   mkPythonEnvs {
#     inherit pkgs lib;
#     python = pkgs.python312;
#     workspaceRoot = ./.;
#     name = "mybench";
#     pyproject-nix = inputs.pyproject-nix;
#     pyproject-build-systems = inputs.pyproject-build-systems;
#     uv2nix = inputs.uv2nix;
#     python-devops-nix = inputs.python-devops-nix;
#     extraOverrides = final: prev: { ... };
#   }

{
  pkgs,
  lib,
  python,
  workspaceRoot,
  name,
  pyproject-nix,
  pyproject-build-systems,
  uv2nix,
  # lib.python: withGrafts (the `.pth` graft), overrides (the package fixes)
  # and lockAudit (the stale-lock sentence).
  python-devops-nix,
  extraOverrides ? (_final: _prev: { }),
  # The packages to graft, each from lib/grafts.nix, or null. devguard (the
  # frappe_devguard package) and benchcli go into the *development* virtualenv
  # only; unixsock, journald and nodebuild into *both*.
  devguard ? null,
  unixsock ? null,
  journald ? null,
  nodebuild ? null,
  benchcli ? null,
  # Overrides the "how to re-lock" half of the stale-lock message; null keeps
  # the bench's (lib/relock-advice.nix).
  lockAuditRelock ? null,
  # `{ <normalized distribution name> = <source>; }` — where a workspace member's
  # sources actually are, when that is not `workspaceRoot/apps/<name>`.
  #
  # App mode's workspace is assembled in the store and its apps/ are mirrors:
  # real directories whose files are symlinks back into the inputs. uv2nix builds
  # each member from its directory, and a *source* path added to the store keeps
  # no references — so those symlinks would dangle inside the sandbox and the
  # build would fail on a pyproject.toml it can plainly see. Pointing src at the
  # input the mirror was made from avoids the import entirely, and keeps the
  # assembled workspace free of the framework's bytes: it is rebuilt every time
  # the app's own source moves, i.e. on every commit.
  srcOverrides ? { },
  # Where the dev env's editable `.pth` files point, as a string the interpreter
  # expands at startup. A bench's members live at `<bench>/apps/<name>`; frappe-nix's
  # own dev env (dev/env.nix) has its project in dev/ and reaches runtime/ as `../`.
  editableRoot ? "$FRAPPE_BENCH_ROOT",
  # The Node that builds what a package builds with Node (see `prodSourceOverlay` below
  # and lib/flow-web-ui.nix). The bench's `frappe-nix.nodejs`.
  nodejs ? pkgs.nodejs,
}:

let
  workspace = uv2nix.lib.workspace.loadWorkspace { inherit workspaceRoot; };

  rootPyproject = builtins.fromTOML (builtins.readFile (workspaceRoot + "/pyproject.toml"));

  # The root package name from pyproject.toml [project].name
  rootPkgName = rootPyproject.project.name;

  # Extract direct runtime dependencies from [project].dependencies
  rootDepNames = map (
    dep: lib.strings.toLower (builtins.head (builtins.match "([A-Za-z0-9_-]+).*" dep))
  ) (rootPyproject.project.dependencies or [ ]);
  rootDepsAttr = lib.genAttrs rootDepNames (_: [ ]);

  # Extract dev-group packages from [dependency-groups]: every group, so dev and
  # bench-dev (each app's [tool.bench.dev-dependencies], what `bench setup
  # requirements --dev` installs; lib/frappe-workspace.py). The dev env only:
  # prodPythonEnv takes [project].dependencies alone. bench-dev carries an
  # app's environment marker as written, so a requirement with one is kept only
  # where it holds for this interpreter; where it does not, the lock resolves
  # nothing for it here. One without a marker is not parsed at all.
  devEnviron = pyproject-nix.lib.pep508.mkEnviron python;
  devReqHolds =
    req:
    !(lib.hasInfix ";" req)
    || (
      let
        parsed = pyproject-nix.lib.pep508.parseString req;
      in
      parsed.markers == null || pyproject-nix.lib.pep508.evalMarkers devEnviron parsed.markers
    );
  rootDevDepNames =
    map (dep: lib.strings.toLower (builtins.head (builtins.match "([A-Za-z0-9_-]+).*" dep)))
      (
        lib.filter (req: builtins.isString req && devReqHolds req) (
          lib.flatten (lib.attrValues (rootPyproject."dependency-groups" or { }))
        )
      );
  rootDevDepsAttr = lib.genAttrs rootDevDepNames (_: [ ]);

  overlay = workspace.mkPyprojectOverlay {
    sourcePreference = "wheel";
  };

  # The packages uv.lock records, as the lock writes them (names normalized).
  lockPackages = (builtins.fromTOML (builtins.readFile (workspaceRoot + "/uv.lock"))).package or [ ];

  # Where a package the lock installs from a directory lives, relative to the
  # workspace root, or null: a workspace member, or a path source such as
  # frappe's flow harness. Not the root (`virtual`), which is never built.
  localPathOf = p: p.source.editable or p.source.directory or null;

  # Packages the lock installs from a directory *inside* an overridden member's
  # directory, as `{ <name> = <src>; }` re-rooted on that member's override.
  #
  # Frappe (from 16.60) keeps its flow harness at `flow/harness`, which the
  # lock records as `apps/frappe/flow/harness`. In app mode `apps/frappe` is a
  # mirror (lib/app-workspace.nix): real directories of symlinks to the input,
  # which dangle in the sandbox for the reason `srcOverrides` exists. The harness
  # is no workspace member, so nothing overrides it by name; it is the input's
  # `flow/harness` all the same.
  nestedSrcOverrides =
    let
      byName = lib.listToAttrs (map (p: lib.nameValuePair p.name p) lockPackages);
      roots = lib.filter (name: byName ? ${name} && localPathOf byName.${name} != null) (
        lib.attrNames srcOverrides
      );
    in
    lib.listToAttrs (
      lib.concatMap (
        p:
        let
          path = localPathOf p;
        in
        lib.optionals (path != null && !(srcOverrides ? ${p.name})) (
          lib.concatMap (
            root:
            let
              rootPath = localPathOf byName.${root} + "/";
            in
            lib.optional (lib.hasPrefix rootPath path) (
              lib.nameValuePair p.name (srcOverrides.${root} + "/${lib.removePrefix rootPath path}")
            )
          ) roots
        )
      ) lockPackages
    );

  # Last, so it wins over uv2nix's own src and over anything extraOverrides did.
  # Applied to the editable set as well, below.
  #
  # An override names the package root itself: app mode passes each app's
  # checkout, the runtime option passes runtime/. The lock, though, may say the
  # package lives in a *subdirectory* of its git source -- frappe-runtime does,
  # under runtime/ of this repository -- and uv2nix honours that with a
  # postUnpack that descends into it (lib/build.nix, `sourceRoot=
  # "$sourceRoot/<subdirectory>"`). Against a src that is already the package,
  # that descent looks for runtime/runtime and the build fails in unpackPhase.
  # uv2nix sets postUnpack for nothing else, so clearing it is exact.
  srcOverlay =
    _final: prev:
    lib.mapAttrs (
      name: src:
      prev.${name}.overrideAttrs (_: {
        inherit src;
        postUnpack = "";
      })
    ) (lib.filterAttrs (name: _: prev ? ${name}) (srcOverrides // nestedSrcOverrides));

  pythonSet =
    (pkgs.callPackage pyproject-nix.build.packages {
      inherit python;
    }).overrideScope
      (
        lib.composeManyExtensions [
          pyproject-build-systems.overlays.default
          overlay
          # The fixes for packages that need a system library (psycopg2,
          # python-ldap, lxml, pycups, python-magic, pyvips, cairocffi), shared
          # with odoo-nix: right after uv2nix's overlay, so frappe-nix's own
          # (mysqlclient) and the bench's pythonOverrides layer on top. A build
          # fix touches an sdist build only; a dlopen fix patches the installed
          # files, so it reaches a wheel too.
          (python-devops-nix.lib.python.overrides { inherit pkgs; })
          extraOverrides
          srcOverlay
        ]
      );

  # Turn a stale uv.lock into a sentence instead of an "attribute 'x' missing"
  # deep inside uv2nix's resolver. Membership is tested against `pythonSet`
  # because that is precisely the set resolvers.nix indexes.
  lockAuditFor =
    root:
    python-devops-nix.lib.python.lockAudit {
      inherit workspaceRoot;
      rootPyproject = root;
      label = "frappe-nix";
      hasPackage = name: pythonSet ? ${name};
      relock = if lockAuditRelock != null then lockAuditRelock else import ./relock-advice.nix;
    };
  lockAudit = lockAuditFor rootPyproject;

  # Wraps both virtualenvs: every consumer — the dev shell, benchRoot, the
  # containers, the NixOS module — reaches uv2nix's resolver through one of them.
  assertLockCurrent = lockAudit.assertCurrent;
  # The production one audits the root without its bench-dev group, which it
  # never installs: that group follows the apps' [tool.bench.dev-dependencies],
  # so an app bump can add a name the committed lock lacks, and that has to stop
  # the dev shell (which installs it) until the relock, not a container or
  # NixOS build (which does not).
  assertProdLockCurrent =
    (lockAuditFor (
      rootPyproject
      // lib.optionalAttrs (rootPyproject ? dependency-groups) {
        dependency-groups = removeAttrs rootPyproject.dependency-groups [ "bench-dev" ];
      }
    )).assertCurrent;

  # Packages grafted into a virtualenv's site-packages with a `.pth` bootstrap
  # (python-devops-nix's lib.python.withGrafts, which writes
  # zzz-<module, _ as ->.pth), so the interpreter runs them at startup: *below*
  # the Frappe app layer, and without an app install or a site_config.json edit.
  # Grafted rather than put on PYTHONPATH because `apps/*` reach sys.path
  # through uv2nix's editable `.pth` files inside the same venv: an interpreter
  # started outside the devenv environment (an editor terminal, `nix run`, CI,
  # a stray `sudo -u`) still imports Frappe, and would otherwise run unpatched.
  graftFor =
    kind:
    # frappe_devguard is DEVELOPMENT ONLY, by construction: it stops a dev bench
    # reaching production services, so reaching production is exactly what it
    # must never be able to do. Do not add it to the prod list.
    # checks.devguard-production asserts prodPythonEnv has none of it.
    lib.optional (kind == "dev" && devguard != null) devguard
    # frappe_unixsock ships to BOTH, deliberately. It carries no policy — only
    # transport corrections for sockets Frappe half-supports — and a socket-mode
    # deployment needs them precisely because it is production. It shares no code
    # with devguard (each graft carries its own copy of _hook.py), so this does
    # not weaken the rule above.
    ++ lib.optional (unixsock != null) unixsock
    # frappe_journald ships to BOTH as well, and is inert in development by
    # construction: it does nothing unless stderr is the journal, which only a
    # systemd unit arranges. Production is where it matters — services.frappe-nix's
    # units log to the journal, and without it every Frappe line lands there at
    # the unit's default priority and bench keeps appending to logs/bench.log.
    ++ lib.optional (journald != null) journald
    # frappe_nodebuild ships to BOTH because builtBench builds its assets with
    # prodPythonEnv's bench. It acts only where FRAPPE_NIX_ESBUILD_PRELOAD is
    # set — the dev shell and builtBench's build phase — and a deployed host
    # never builds, so there it does nothing.
    ++ lib.optional (nodebuild != null) nodebuild
    # frappe_benchcli is DEVELOPMENT ONLY: it hands `bench update` and `bench
    # build` to the dev shell's bench-update and bench-build, which a deployed
    # host does not have, and whose units call this venv's bench for workers
    # and the scheduler.
    ++ lib.optional (kind == "dev" && benchcli != null) benchcli;

  withGrafts =
    kind: env:
    python-devops-nix.lib.python.withGrafts {
      inherit python env;
      grafts = graftFor kind;
    };

  # The packages uv.lock installs editable, by distribution name: workspace
  # members, and a path source such as frappe's flow harness.
  editableMembers = map (p: p.name) (lib.filter (p: p ? source && p.source ? editable) lockPackages);

  # Every package built from a source tree, which is every package whose build
  # is the project's own: the apps, and whatever else the lock installs from a
  # directory (frappe's flow harness). A wheel and an sdist from an index are
  # not: their build is the index's.
  localSrcNames = lib.filter (n: n != rootPkgName) (
    map (p: p.name) (lib.filter (p: localPathOf p != null) lockPackages)
  );

  # lib/flow-web-ui.nix, for the package whose build hook packages a web UI by
  # running npm, which the sandbox cannot do against a registry: the UI is built
  # offline from its lock on its own, and the package is given it as a source
  # distribution would carry it.
  flowWebUi = import ./flow-web-ui.nix {
    inherit pkgs lib nodejs;
    node = python-devops-nix.lib.node;
  };

  # What the flow harness is given: its UI, as a source distribution carries it.
  # The build hook (hatch_build.py) takes a tree with a PKG-INFO for an sdist and
  # only checks that the UI is there. hatchling reads that file as the core
  # metadata of the sdist, and refuses one without a Name and a Version, so it
  # is the minimal one an sdist of this package would hold, from the lock's own
  # name and version. Nothing when the harness is not the one
  # lib/flow-web-ui.nix knows.
  flowHarnessAttrs =
    old:
    let
      webui = flowWebUi old.src;
    in
    lib.optionalAttrs (webui != null) {
      postPatch = (old.postPatch or "") + ''
        mkdir -p src/flow_harness/server
        cp -r --no-preserve=mode ${webui} src/flow_harness/server/webui
        printf 'Metadata-Version: 2.1\nName: %s\nVersion: %s\n' \
          ${lib.escapeShellArg old.pname} ${lib.escapeShellArg old.version} > PKG-INFO
      '';
      passthru = (old.passthru or { }) // {
        inherit webui;
      };
    };

  prodSourceOverlay =
    _final: prev:
    lib.genAttrs (lib.filter (n: prev ? ${n}) localSrcNames) (
      n:
      prev.${n}.overrideAttrs (
        old:
        {
          # Node and npm in the build of every package built from source. A
          # fallback, and a narrow one: there is still no network, so it serves
          # a build hook that works from what is in the tree (a prebuilt
          # bundle, a script that only bundles) and fails fast on one that
          # installs. What needs an install is built beforehand, like the
          # harness's UI. Wheels and sdists from an index are untouched, so this
          # changes the apps' derivations and nothing else's.
          nativeBuildInputs = (old.nativeBuildInputs or [ ]) ++ [ nodejs ];
          env = python-devops-nix.lib.node.sandboxEnv // (old.env or { });
        }
        // lib.optionalAttrs (n == "frappe-flow") (flowHarnessAttrs old)
      )
    );

  # The production set: pythonSet plus what only a build from source needs. Not in
  # the editable set, whose build packages nothing, so a development shell never
  # builds a web UI.
  prodPythonSet = pythonSet.overrideScope prodSourceOverlay;

  # Production: workspace members + runtime deps, no dev tools
  prodPythonEnv = assertProdLockCurrent (
    withGrafts "prod" (
      prodPythonSet.mkVirtualEnv "${name}-bench-prod-env" (
        lib.filterAttrs (name: _: name != rootPkgName) workspace.deps.default // rootDepsAttr
      )
    )
  );

  # An editable member's source, cut down to what its editable build reads, so
  # an edit to an app's code stops rebuilding the virtualenv. See
  # lib/editable-src.nix.
  trimEditableSrc = import ./editable-src.nix { inherit lib; };

  editableSrcOverlay =
    _final: prev:
    lib.genAttrs (lib.filter (name: prev ? ${name}) editableMembers) (
      name:
      prev.${name}.overrideAttrs (old: {
        src = trimEditableSrc old.src;
      })
    );

  # Development: adds editable overlay so workspace packages resolve from source
  editablePythonSet = pythonSet.overrideScope (
    lib.composeManyExtensions [
      (workspace.mkEditablePyprojectOverlay {
        # The *bench*, not the git worktree. In a bench repo the two are the same
        # directory, so this is a no-op there; in app mode they are not, and the
        # editable finder resolves `<root>/apps/<name>`, which is a bench path by
        # construction. REPO_ROOT keeps its literal meaning — the worktree where
        # secrets/*.age are tracked and `git add` has to run.
        root = editableRoot;
      })
      editableSrcOverlay
      # `editables`, for the editable build of the root and of every package the
      # lock installs editable. hatchling imports it in its PEP 660 hook but
      # lists it only from `get_requires_for_build_editable`, at build time, so
      # neither a package's [build-system].requires nor uv.lock names it and
      # uv2nix does not provide it: the build dies on `No module named
      # 'editables'`. The root has always needed it; frappe's flow harness
      # (hatchling, a path source of frappe's own) is not the last package to.
      # Every package uv2nix's editable overlay above touches gets it, which is
      # every local one (a `directory` source as much as an `editable` one). Any
      # other backend ignores it.
      (final: prev: {
        ${rootPkgName} = prev.${rootPkgName}.overrideAttrs (old: {
          nativeBuildInputs = (old.nativeBuildInputs or [ ]) ++ [
            final.editables
          ];
        });
      })
      (
        final: prev:
        lib.genAttrs (lib.filter (n: prev ? ${n}) localSrcNames) (
          n:
          prev.${n}.overrideAttrs (old: {
            nativeBuildInputs = (old.nativeBuildInputs or [ ]) ++ [ final.editables ];
          })
        )
      )
      # After the editable overlay, which rewrites each member's build phases but
      # keeps reading its sources from src.
      srcOverlay
    ]
  );

  baseDevPythonEnv = editablePythonSet.mkVirtualEnv "${name}-bench-dev-env" (
    lib.filterAttrs (name: _: name != rootPkgName) (workspace.deps.default // workspace.deps.groups)
    // rootDepsAttr
    // rootDevDepsAttr
  );

  # Development: the guards (frappe_devguard) plus the socket corrections
  # (frappe_unixsock) and the journald formatting (frappe_journald). See
  # graftFor above for why devguard alone is kept out of prodPythonEnv.
  devPythonEnv = assertLockCurrent (withGrafts "dev" baseDevPythonEnv);

in
{
  inherit
    pythonSet
    prodPythonSet
    prodPythonEnv
    devPythonEnv
    editablePythonSet
    workspace
    rootPyproject
    rootPkgName
    # Normalized [project].dependencies names, for a consumer asking whether the
    # root declares a package before it forces an env that would lack it.
    rootDepNames
    ;
}
