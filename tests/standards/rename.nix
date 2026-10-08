# N1's frappe-rename-app checks (docs/app-standards/spec.md §5.10, §7 "N1: runtime"),
# the parts that need no database. selftest-runtime.yml renames the fixture app
# on a real site.
#
#   standards-rename-code    `code` on tests/fixtures/rename-esign: --dry-run
#                           prints the diff and changes nothing; the real run
#                           renames the OLD.* bundles (not a DocType named like
#                           the app, whose doctype/OLD/OLD.* files keep their
#                           names), leaves every hooks.py
#                           asset naming an existing file, leaves a bare name
#                           with no file alone and reports it, rewrites the
#                           names, bumps `modified`, adds the shim; a dirty tree
#                           and a second run are refused with exit 1. Replace
#                           pairs: frappe-nix has none of its own; a fleet
#                           file's replace entry, a profile's [[replace-apps]]
#                           (named with --profile, or the app's own once it has
#                           [tool.frappe-nix]) are refused with exit 1, and an
#                           app without [tool.frappe-nix] that names no source
#                           exits 2 and changes nothing.
#   standards-rename-nixos   services.frappe.sites.<site>.{renamedApps,replacedApps}:
#                           the migrate unit's script, run against a stub bench
#                           and database, renames after maintenance mode goes on
#                           and before the migrate, installs the stand-in new app
#                           and uninstalls the old one inside the snapshot, and
#                           does nothing to the apps on a second run.
#   standards-rename-devenv  frappe-nix.{renamedApps,replacedApps}: reconcile-apps
#                           (the script and the devenv-up task) renames before it
#                           installs anything, and replaces after.
{
  pkgs,
  lib,
  self,
  frappeNixTools,
  ...
}:

let
  inherit (pkgs.stdenv.hostPlatform) system;
  tool = ../../lib/rename/frappe_rename_app.py;
  # The code half's interpreter, as lib/scripts.d/frappe-rename-app.nix has it.
  codePython = frappeNixTools.pythonModule.withPackages (_: [ frappeNixTools ]);

  # An org profile whose [[replace-apps]] names a pair, and a fleet file whose
  # entry does (spec §4.9, §8.1). Fictitious values.
  replaceProfile = pkgs.writeTextDir "profile.toml" ''
    schema = 1
    name = "example-org"
    extends = "recommended@1.0"

    [[replace-apps]]
    from = "jailbreak"
    to = "data_steward"
  '';
  replaceFleet = pkgs.writeText "fleet.json" (
    builtins.toJSON {
      schema = 1;
      apps = [
        {
          repo = "example/jailbreak";
          app = "jailbreak";
          target_app = "data_steward";
          rename_mode = "replace";
        }
        {
          repo = "example/esign";
          app = "esign";
        }
      ];
    }
  );

  # --- NixOS ------------------------------------------------------------------

  site = "erp.example.com";

  # A bench whose `bench` and `python` log every call and keep the site's
  # installed apps in $STATE/installed, and a database whose clients succeed.
  # STUB_FAIL_RENAME=1 fails frappe_rename_app.py, STUB_FAIL_INSTALL=<app> that
  # app's install-app, and STUB_INSTALL_DELAY makes install-app take that long.
  stubEnv = pkgs.runCommand "stub-bench-env" { } ''
    mkdir -p "$out/bin"
    cat > "$out/bin/bench" <<'EOF'
    #!${lib.getExe pkgs.bash}
    echo "bench $*" >> "$STATE/log"
    case "$*" in
      *" list-apps --format json") printf '{"%s": [' "$2"; sed 's/.*/"&"/' "$STATE/installed" | paste -sd, | tr -d '\n'; printf ']}\n' ;;
      *" install-app "*)
        [ "''${STUB_FAIL_INSTALL:-}" != "''${4}" ] || exit 1
        sleep "''${STUB_INSTALL_DELAY:-0}"
        echo "''${4}" >> "$STATE/installed" ;;
      *" uninstall-app "*) grep -vx "''${4}" "$STATE/installed" > "$STATE/installed.new"; mv "$STATE/installed.new" "$STATE/installed" ;;
    esac
    EOF
    cat > "$out/bin/python" <<'EOF'
    #!${lib.getExe pkgs.bash}
    echo "python $* (in $PWD)" >> "$STATE/log"
    case "$*" in *frappe_rename_app.py*) [ -z "''${STUB_FAIL_RENAME:-}" ] || exit 1 ;; esac
    EOF
    chmod +x "$out/bin/"*
  '';
  stubBench =
    pkgs.runCommand "stub-bench"
      {
        passthru = {
          inherit (pkgs) nodejs;
          pythonEnv = stubEnv;
          appsPath = _: "/stub/apps";
        };
      }
      ''
        mkdir -p $out/bench
      '';
  stubDatabase =
    pkgs.runCommand "stub-mariadb"
      {
        passthru.client = pkgs.emptyDirectory;
      }
      ''
        mkdir -p "$out/bin"
        printf '#!%s\necho "mysql $*" >> "$STATE/log"\ncase "$*" in *COUNT*) echo 7 ;; *) cat > /dev/null ;; esac\n' ${lib.getExe pkgs.bash} > "$out/bin/mysql"
        printf '#!%s\necho "mysqldump $*" >> "$STATE/log"\necho "-- dump"\n' ${lib.getExe pkgs.bash} > "$out/bin/mysqldump"
        chmod +x "$out/bin/"*
      '';

  nixos =
    (import (pkgs.path + "/nixos/lib/eval-config.nix") {
      inherit system;
      modules = [
        self.nixosModules.default
        {
          nixpkgs.pkgs = pkgs;
          system.stateVersion = lib.trivial.release;
          services.frappe = {
            enable = true;
            package = stubBench;
            database.package = stubDatabase;
            sites.${site} = {
              enable = true;
              siteDir = "@SITEDIR@";
              renamedApps.esign = "esign_webforms";
              replacedApps.old_app = "new_app";
            };
          };
        }
      ];
    }).config;
  migrateScript = nixos.systemd.services."frappe-migrate-${site}".serviceConfig.ExecStart;
  dbName = nixos.services.frappe.sites.${site}.database.name;

  # --- devenv -----------------------------------------------------------------

  # The wrapper modules/devenv.nix builds, over a stub reconcile-apps body.
  # The stub logs what lib/scripts.nix's body would install: every app in
  # sites/apps.txt the site lacks, but for FRAPPE_NIX_RECONCILE_SKIP.
  reconcile = import ../../lib/rename/reconcile.nix { inherit pkgs lib; } {
    reconcileExec = ''
      echo "reconcile $*" >> "$STATE/log"
      [ -d "$FRAPPE_BENCH_ROOT/sites/$FRAPPE_SITE" ] || exit 0
      while read -r app; do
        case " ''${FRAPPE_NIX_RECONCILE_SKIP:-} " in *" $app "*) continue ;; esac
        grep -qxF "$app" "$STATE/installed" || echo "reconcile would install $app" >> "$STATE/log"
      done < "$FRAPPE_BENCH_ROOT/sites/apps.txt"
    '';
    pythonBin = "${stubEnv}/bin/python";
    benchBin = "${stubEnv}/bin/bench";
    renamedApps.esign = "esign_webforms";
    replacedApps.old_app = "new_app";
  };
  plain = import ../../lib/rename/reconcile.nix { inherit pkgs lib; } {
    reconcileExec = "echo plain";
    pythonBin = "python";
    benchBin = "bench";
    renamedApps = { };
    replacedApps = { };
  };
  devenvSource = builtins.readFile ../../modules/devenv.nix;
  # Not lib.hasInfix: its `.*infix.*` regex over a 150 KB file overflows the
  # regex engine's stack on some Nix builds (2.35 on CI's runners).
  contains = needle: haystack: builtins.replaceStrings [ needle ] [ "" ] haystack != haystack;
in
{
  standards-rename-code =
    pkgs.runCommand "standards-rename-code-check"
      {
        nativeBuildInputs = [
          pkgs.git
          pkgs.python3
        ];
      }
      ''
        set -euo pipefail
        fail() { echo "FAIL $*" >&2; exit 1; }
        export HOME="$PWD" GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t
        cp -r ${../fixtures/rename-esign} repo
        chmod -R u+w repo
        cd repo
        git init -q && git add -A && git commit -qm fixture
        rename() { ${lib.getExe codePython} ${tool} code "$@"; }

        # The fixture has no [tool.frappe-nix]: it must say where its replace
        # pairs come from, or that there are none to check.
        set +e
        rename --from esign --to esign_webforms 2> ../err; rc=$?
        set -e
        [ "$rc" = 2 ] && grep -q -- '--fleet <file>, --profile <ref>, or --no-replace-check' ../err \
          || fail "no replace source: exit $rc, $(cat ../err)"
        [ -z "$(git status --porcelain)" ] || fail "no replace source: the tree changed"
        echo "ok   an app without [tool.frappe-nix] and no --fleet, --profile or --no-replace-check exits 2, unchanged"

        rename --from esign --to esign_webforms --no-replace-check --dry-run > ../dry.log
        [ -z "$(git status --porcelain --untracked-files=no)" ] || fail "--dry-run changed the tree"
        grep -q 'rename from esign/public/js/esign.desk.bundle.js' ../dry.log || fail "--dry-run: no bundle rename in the diff"
        grep -q 'rename to esign_webforms/public/js/esign_webforms.desk.bundle.js' ../dry.log || fail "--dry-run: wrong bundle target"
        grep -q '^+app_name = "esign_webforms"' ../dry.log || fail "--dry-run: app_name not in the diff"
        echo "ok   code --dry-run prints the diff and changes nothing"

        echo stray > stray.txt
        if rename --from esign --to esign_webforms --no-replace-check 2> ../err; then fail "a dirty tree was renamed"; fi
        grep -q 'uncommitted changes' ../err || fail "dirty tree: $(cat ../err)"
        rm stray.txt
        echo "ok   a dirty tree is refused"

        replaced() { # <label> <args…>: a replace pair, refused before anything moves
          local label="$1" rc=0
          shift
          rename --from jailbreak --to data_steward "$@" 2> ../err || rc=$?
          [ "$rc" = 1 ] && grep -q 'data_steward is a new app installed beside it' ../err \
            || fail "$label: exit $rc, $(cat ../err)"
          [ -z "$(git status --porcelain)" ] || fail "$label: the tree changed"
          echo "ok   code --from jailbreak --to data_steward $label exits 1 (a replace pair)"
        }
        replaced "--fleet <a fleet with that replace entry>" --fleet ${replaceFleet}
        replaced "--profile <a profile whose [[replace-apps]] has it>" --profile ${replaceProfile}
        # frappe-nix itself knows no pair: without a source that names it, the
        # pair is only checked against what was given.
        rc=0; rename --from jailbreak --to data_steward --profile recommended 2> ../err || rc=$?
        [ "$rc" = 1 ] && grep -q 'nothing named jailbreak' ../err || fail "a built-in profile has a replace pair: $(cat ../err)"
        echo "ok   no built-in replace pair: --profile recommended refuses nothing"

        # The same app once it has opted in with that profile (in-repo, ./<dir>):
        # its own [[replace-apps]] apply with no flag at all.
        git clone -q . ../opted
        (
          cd ../opted
          cp -r ${replaceProfile} .standards-profile
          chmod -R u+w .standards-profile
          printf '\n[tool.frappe-nix]\nschema = 1\nfrappe-major = 16\nprofile = "./.standards-profile"\n' >> pyproject.toml
          git add -A && git commit -qm opt-in
          rc=0; rename --from jailbreak --to data_steward 2> ../err || rc=$?
          [ "$rc" = 1 ] && grep -q 'data_steward is a new app installed beside it' ../err \
            || fail "opted in with the pair in [[replace-apps]]: exit $rc, $(cat ../err)"
          [ -z "$(git status --porcelain)" ] || fail "opted in: the tree changed"
        )
        echo "ok   in an app synced with that profile, code --from jailbreak --to data_steward exits 1 without --fleet"

        rename --from esign --to esign_webforms --fleet ${replaceFleet} > ../run.log
        cat ../run.log
        [ -d esign_webforms/esign ] && [ ! -e esign ] || fail "the package or its module folder moved wrongly"
        [ "$(cat esign_webforms/modules.txt)" = eSign ] || fail "modules.txt changed"
        for f in public/js/esign_webforms.desk.bundle.js public/js/esign_webforms.control.bundle.js \
                 public/css/esign_webforms.control.bundle.css public/js/web/esign_webforms.web.bundle.js; do
          [ -f "esign_webforms/$f" ] || fail "esign_webforms/$f is missing"
        done
        [ ! -e esign_webforms/public/js/esign.desk.bundle.js ] || fail "esign.desk.bundle.js is still there"
        echo "ok   the OLD.* bundles are NEW.*"
        # The eSign DocType scrubs to the app's name: frappe loads it from
        # doctype/esign/esign.{json,py,js}, so those keep their names.
        for f in esign.json esign.py esign.js; do
          [ -f "esign_webforms/esign/doctype/esign/$f" ] || fail "the eSign DocType's $f was renamed: $(ls esign_webforms/esign/doctype/esign)"
        done
        ! ls esign_webforms/esign/doctype/esign | grep -q esign_webforms || fail "a file of the eSign DocType took the new name"
        grep -q '^from frappe.model.document import Document$' esign_webforms/esign/doctype/esign/esign.py || fail "the DocType's controller changed"
        echo "ok   a DocType named like the app keeps its folder and files"

        # Every asset hooks.py names by file name exists, except the one that never did.
        python3 - <<'PY'
        import ast, pathlib, sys
        tree = ast.parse(pathlib.Path("esign_webforms/hooks.py").read_text())
        names = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.List):
                names[node.targets[0].id] = [e.value for e in node.value.elts if isinstance(e, ast.Constant)]
        files = {p.name for p in pathlib.Path("esign_webforms/public").rglob("*")}
        for key in ("app_include_js", "app_include_css", "web_include_js"):
            for name in names[key]:
                if not name.startswith("/") and name not in files:
                    sys.exit(f"FAIL hooks.py {key} names {name}, which does not exist")
        if names["web_include_css"] != ["esign.legacy.bundle.css"]:
            sys.exit(f"FAIL the bare name was rewritten: {names['web_include_css']}")
        print("ok   every app_include_js/css and web_include_js entry names an existing file")
        PY
        grep -q 'esign_webforms/hooks.py:[0-9]*: esign.legacy.bundle.css' ../run.log || fail "the bare esign.legacy.bundle.css is not reported"
        echo "ok   the bare esign.legacy.bundle.css is left alone and reported"

        grep -qx 'app_name = "esign_webforms"' esign_webforms/hooks.py || fail "app_name"
        grep -qx 'name = "esign_webforms"' pyproject.toml || fail "[project].name"
        grep -q '"name": "esign_webforms"' package.json || fail "package.json name"
        grep -q '"package-name": "esign_webforms"' release-please-config.json || fail "release-please package-name"
        grep -q -- '--app esign_webforms' .github/workflows/ci.yml || fail "the CI --app"
        grep -qx 'esign_webforms.patches.v1.fix_signatures' esign_webforms/patches.txt || fail "patches.txt"
        grep -qxF 'execute:frappe.db.set_value("Web Form", "esign-demo", "client_script", "esign.check()")' esign_webforms/patches.txt \
          || fail "patches.txt: the JS namespace in an execute: line's string was rewritten"
        grep -q '^esign\.accept = ' esign_webforms/public/js/esign_webforms.desk.bundle.js || fail "the JS namespace esign.accept was rewritten"
        grep -q 'from esign_webforms.esign import' esign_webforms/esign/custom/web_form.py || fail "the import"
        grep -q '"esign_webforms/templates/esign_webforms.html"' esign_webforms/esign/custom/web_form.py || fail "the template path"
        grep -q '/api/method/esign_webforms.esign.custom.web_form.accept' esign_webforms/esign/custom/web_form.py || fail "the /api/method URL"
        grep -q '^import esign_webforms$' esign_webforms/patches/v1/fix_signatures.py || fail "import OLD"
        grep -q 'localStorage.getItem("esign_signature_pad")' esign_webforms/public/js/esign_webforms.desk.bundle.js || fail "a localStorage key changed"
        cmp -s CHANGELOG.md ${../fixtures/rename-esign/CHANGELOG.md} || fail "CHANGELOG.md changed"
        echo "ok   names, dotted paths, patches, imports and URLs rewritten; history, keys and JS namespaces kept"

        # The site half rewrites what the code half does: OLD.<module of NEW>, not a
        # JS namespace (the bundle above still defines esign), and the Patch Log line
        # equals the patches.txt line.
        python3 - ${tool} <<'PY'
        import importlib.util, pathlib, sys
        spec = importlib.util.spec_from_file_location("fra", sys.argv[1])
        fra = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fra)
        mods = fra.module_names(pathlib.Path("esign_webforms"))
        cases = {
            "esign.accept(frm.doc.name)": "esign.accept(frm.doc.name)",
            'frappe.call({method: "esign.esign.custom.web_form.accept"})': 'frappe.call({method: "esign_webforms.esign.custom.web_form.accept"})',
            '<link href="/assets/esign/dist/x.css">': '<link href="/assets/esign_webforms/dist/x.css">',
            "esign.config.after_install": "esign_webforms.config.after_install",
            "esign.legacy.bundle.css": "esign.legacy.bundle.css",
        }
        for text, want in cases.items():
            got = fra.rewrite_text(text, "esign", "esign_webforms", mods)
            if got != want:
                sys.exit(f"FAIL site half: {text!r} -> {got!r}, want {want!r}")
        PY
        python3 - ${tool} ${../fixtures/rename-esign/esign/patches.txt} <<'PY'
        import importlib.util, pathlib, sys
        spec = importlib.util.spec_from_file_location("fra", sys.argv[1])
        fra = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fra)
        mods = fra.module_names(pathlib.Path("esign_webforms"))
        before = pathlib.Path(sys.argv[2]).read_text().splitlines()
        after = pathlib.Path("esign_webforms/patches.txt").read_text().splitlines()
        site = [fra.rewrite_paths(line, "esign", "esign_webforms", mods) for line in before]
        if site != after:
            sys.exit(f"FAIL the Patch Log rewrite {site} is not patches.txt's {after}")
        PY
        echo "ok   the site half keeps JS namespaces and file names, and matches patches.txt line for line"

        json=esign_webforms/esign/web_form/esign_demo/esign_demo.json
        grep -q '/assets/esign_webforms/dist/esign-fonts.css' "$json" || fail "the JSON's asset URL"
        ! grep -q '"modified": "2026-01-01 00:00:00.000000"' "$json" || fail "modified not bumped"
        echo "ok   the standard JSON's modified is bumped"

        grep -q '^# frappe-nix:rename-shim-begin esign$' esign_webforms/hooks.py || fail "no shim"
        grep -q '"esign.esign.custom.web_form.accept": "esign_webforms.esign.custom.web_form.accept",' esign_webforms/hooks.py || fail "shim entry"
        python3 -c 'import ast,sys; ast.parse(open(sys.argv[1]).read())' esign_webforms/hooks.py
        echo "ok   the override_whitelisted_methods shim"

        git commit -qm renamed
        if rename --from esign --to esign_webforms --no-replace-check 2> ../err; then fail "a second run renamed something"; fi
        grep -q 'nothing named esign' ../err || fail "second run: $(cat ../err)"
        echo "ok   a second run has nothing to rename"
        touch "$out"
      '';

  standards-rename-nixos = pkgs.runCommand "standards-rename-nixos-check" { } ''
    set -euo pipefail
    fail() { echo "FAIL $*" >&2; exit 1; }
    export STATE="$PWD/state"
    mkdir -p "$STATE" site/bench/sites "site/sites/${site}"
    echo '{"db_password": "x"}' > "site/sites/${site}/site_config.json"
    sed "s|@SITEDIR@|$PWD/site|g" ${migrateScript} > migrate
    chmod +x migrate
    printf '%s\n' frappe esign old_app > "$STATE/installed"

    ./migrate
    cat "$STATE/log"
    line() { grep -nF -- "$1" "$STATE/log" | head -1 | cut -d: -f1; }
    snap="$(line mysqldump)" on="$(line 'set-maintenance-mode on')" ren="$(line 'frappe_rename_app.py')"
    inst="$(line 'install-app new_app')" uninst="$(line 'uninstall-app old_app --yes --no-backup')"
    mig="$(line ' migrate')" off="$(line 'set-maintenance-mode off')"
    for v in snap on ren inst uninst mig off; do [ -n "''${!v}" ] || fail "no $v step"; done
    [ "$snap" -lt "$on" ] && [ "$on" -lt "$ren" ] && [ "$ren" -lt "$inst" ] && [ "$inst" -lt "$uninst" ] \
      && [ "$uninst" -lt "$mig" ] && [ "$mig" -lt "$off" ] || fail "order: snapshot $snap, maintenance $on, rename $ren, install $inst, uninstall $uninst, migrate $mig, off $off"
    grep -q "frappe_rename_app.py --site ${site} --yes esign=esign_webforms (in $PWD/site/bench/sites)" "$STATE/log" \
      || fail "the rename does not run from the bench's sites/"
    [ "$(sort "$STATE/installed" | tr '\n' ' ')" = "esign frappe new_app " ] || fail "installed: $(cat "$STATE/installed")"
    echo "ok   renamed after maintenance on, new_app installed and old_app uninstalled inside the snapshot, then migrated"

    # The next deploy: the build marker would skip it, so drop it.
    rm -f site/.frappe-migrate-build
    : > "$STATE/log"
    ./migrate
    ! grep -q 'install-app\|uninstall-app' "$STATE/log" || fail "a second run touched the apps: $(cat "$STATE/log")"
    grep -q ' migrate' "$STATE/log" || fail "a second run did not migrate"
    echo "ok   a second run installs and uninstalls nothing"

    # A failed step: nothing after it runs, the snapshot comes back, and the
    # site stays in maintenance mode.
    failed() { # <label> <env…>
      local label="$1" rc=0
      shift
      rm -f site/.frappe-migrate-build
      printf '%s\n' frappe esign old_app > "$STATE/installed"
      : > "$STATE/log"
      env "$@" ./migrate || rc=$?
      [ "$rc" != 0 ] || fail "$label: the migrate unit exited 0"
      ! grep -q 'uninstall-app' "$STATE/log" || fail "$label: uninstalled an app: $(cat "$STATE/log")"
      ! grep -q ' migrate$' "$STATE/log" || fail "$label: ran bench migrate: $(cat "$STATE/log")"
      ! grep -q 'set-maintenance-mode off' "$STATE/log" || fail "$label: maintenance mode went off"
      grep -q "CONCAT('DROP " "$STATE/log" || fail "$label: no rollback (drop): $(cat "$STATE/log")"
      grep -q '^mysql .* ${dbName}$' "$STATE/log" || fail "$label: no rollback (re-import): $(cat "$STATE/log")"
      echo "ok   $label: exit $rc, no uninstall, no migrate, rolled back, maintenance left on"
    }
    failed "a failed rename" STUB_FAIL_RENAME=1
    ! grep -q 'install-app' "$STATE/log" || fail "a failed rename still installed: $(cat "$STATE/log")"
    failed "a failed install-app new_app" STUB_FAIL_INSTALL=new_app
    grep -q 'install-app new_app' "$STATE/log" || fail "the install was not tried"
    cp ${migrateScript} "$out"
  '';

  standards-rename-devenv =
    assert lib.assertMsg (
      plain == "echo plain"
    ) "reconcile-apps changes with no renamed or replaced apps";
    assert lib.assertMsg
      (contains "case \" ''\${FRAPPE_NIX_RECONCILE_SKIP:-} \" in *\" $app \"*) continue ;; esac" (
        builtins.readFile ../../lib/scripts.nix
      ))
      "lib/scripts.nix: reconcile-apps does not skip FRAPPE_NIX_RECONCILE_SKIP";
    assert lib.assertMsg (
      contains "reconcile-apps = scripts.reconcile-apps // {\n                  exec = reconcileAppsExec;" devenvSource
      && contains "\${reconcileAppsExec}" devenvSource
    ) "devenv.nix: reconcile-apps or the frappe:apps-reconcile task does not use the wrapper";
    pkgs.runCommand "standards-rename-devenv-check" { } ''
      set -euo pipefail
      fail() { echo "FAIL $*" >&2; exit 1; }
      export STATE="$PWD/state" FRAPPE_BENCH_ROOT="$PWD/bench" FRAPPE_SITE=dev.localhost
      mkdir -p "$STATE" bench/sites/dev.localhost
      printf '%s\n' frappe esign old_app > "$STATE/installed"
      # Both are on the bench while the replacement runs.
      printf '%s\n' frappe esign new_app old_app > bench/sites/apps.txt
      cp ${pkgs.writeText "reconcile-apps" reconcile} reconcile-apps

      ${lib.getExe pkgs.bash} reconcile-apps
      cat "$STATE/log"
      line() { grep -nF -- "$1" "$STATE/log" | head -1 | cut -d: -f1; }
      ren="$(line 'frappe_rename_app.py --site dev.localhost --yes esign=esign_webforms')"
      rec="$(line 'reconcile')" inst="$(line 'install-app new_app')" un="$(line 'uninstall-app old_app --yes --no-backup')"
      for v in ren rec inst un; do [ -n "''${!v}" ] || fail "no $v step"; done
      [ "$ren" -lt "$rec" ] && [ "$rec" -lt "$inst" ] && [ "$inst" -lt "$un" ] || fail "order: rename $ren, reconcile $rec, install $inst, uninstall $un"
      grep -q "(in $PWD/bench/sites)" "$STATE/log" || fail "the rename does not run from the bench's sites/"
      echo "ok   reconcile-apps renames first, reconciles, then installs new_app and uninstalls old_app"

      : > "$STATE/log"
      ${lib.getExe pkgs.bash} reconcile-apps
      ! grep -q 'install-app' "$STATE/log" || fail "a second run touched the apps: $(cat "$STATE/log")"
      ! grep -q 'would install old_app' "$STATE/log" || fail "reconcile-apps would install the replaced old_app again: $(cat "$STATE/log")"
      echo "ok   a second run installs and uninstalls nothing, and reconcile-apps leaves the replaced app alone"

      # Every process that waits on frappe:apps-reconcile runs it, at once: one
      # install and one uninstall between them.
      printf '%s\n' frappe esign old_app > "$STATE/installed"
      : > "$STATE/log"
      STUB_INSTALL_DELAY=1 ${lib.getExe pkgs.bash} reconcile-apps & a=$!
      STUB_INSTALL_DELAY=1 ${lib.getExe pkgs.bash} reconcile-apps & b=$!
      STUB_INSTALL_DELAY=1 ${lib.getExe pkgs.bash} reconcile-apps & c=$!
      wait "$a" && wait "$b" && wait "$c" || fail "a concurrent copy failed: $(cat "$STATE/log")"
      [ "$(grep -c 'install-app new_app' "$STATE/log")" = 1 ] \
        && [ "$(grep -c 'uninstall-app old_app' "$STATE/log")" = 1 ] \
        || fail "three copies at once: $(cat "$STATE/log")"
      [ "$(sort "$STATE/installed" | tr '\n' ' ')" = "esign frappe new_app " ] || fail "installed: $(cat "$STATE/installed")"
      echo "ok   three copies at once: one install-app, one uninstall-app (reconcile-apps' lock)"

      rm -rf bench/sites/dev.localhost
      : > "$STATE/log"
      ${lib.getExe pkgs.bash} reconcile-apps
      [ "$(cat "$STATE/log")" = "reconcile " ] || fail "with no site, it did more than reconcile: $(cat "$STATE/log")"
      echo "ok   with no site yet, only reconcile-apps itself runs"
      touch "$out"
    '';
}
