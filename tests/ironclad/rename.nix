# N1's frappe-rename-app checks (docs/ironclad/spec.md §5.10, §7 "N1: runtime"),
# the parts that need no database. selftest-runtime.yml renames the fixture app
# on a real site.
#
#   ironclad-rename-code    `code` on tests/fixtures/rename-esign: --dry-run
#                           prints the diff and changes nothing; the real run
#                           renames the OLD.* bundles, leaves every hooks.py
#                           asset naming an existing file, leaves a bare name
#                           with no file alone and reports it, rewrites the
#                           names, bumps `modified`, adds the shim; a dirty tree,
#                           a replace pair (jailbreak → data_steward) and a
#                           second run are refused with exit 1.
#   ironclad-rename-nixos   services.frappe.sites.<site>.{renamedApps,replacedApps}:
#                           the migrate unit's script, run against a stub bench
#                           and database, renames after maintenance mode goes on
#                           and before the migrate, installs the stand-in new app
#                           and uninstalls the old one inside the snapshot, and
#                           does nothing to the apps on a second run.
#   ironclad-rename-devenv  frappe-nix.{renamedApps,replacedApps}: reconcile-apps
#                           (the script and the devenv-up task) renames before it
#                           installs anything, and replaces after.
{
  pkgs,
  lib,
  self,
  ...
}:

let
  inherit (pkgs.stdenv.hostPlatform) system;
  tool = ../../lib/rename/frappe_rename_app.py;

  # --- NixOS ------------------------------------------------------------------

  site = "erp.example.com";

  # A bench whose `bench` and `python` log every call and keep the site's
  # installed apps in $STATE/installed, and a database whose clients succeed.
  stubEnv = pkgs.runCommand "stub-bench-env" { } ''
    mkdir -p "$out/bin"
    cat > "$out/bin/bench" <<'EOF'
    #!${lib.getExe pkgs.bash}
    echo "bench $*" >> "$STATE/log"
    case "$*" in
      *" list-apps --format json") printf '{"%s": [' "$2"; sed 's/.*/"&"/' "$STATE/installed" | paste -sd, | tr -d '\n'; printf ']}\n' ;;
      *" install-app "*) echo "''${4}" >> "$STATE/installed" ;;
      *" uninstall-app "*) grep -vx "''${4}" "$STATE/installed" > "$STATE/installed.new"; mv "$STATE/installed.new" "$STATE/installed" ;;
    esac
    EOF
    cat > "$out/bin/python" <<'EOF'
    #!${lib.getExe pkgs.bash}
    echo "python $* (in $PWD)" >> "$STATE/log"
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
              replacedApps.jailbreak = "data_steward";
            };
          };
        }
      ];
    }).config;
  migrateScript = nixos.systemd.services."frappe-migrate-${site}".serviceConfig.ExecStart;

  # --- devenv -----------------------------------------------------------------

  # The wrapper modules/devenv.nix builds, over a stub reconcile-apps body.
  reconcile = import ../../lib/rename/reconcile.nix { inherit pkgs lib; } {
    reconcileExec = ''echo "reconcile $*" >> "$STATE/log"'';
    pythonBin = "${stubEnv}/bin/python";
    benchBin = "${stubEnv}/bin/bench";
    renamedApps.esign = "esign_webforms";
    replacedApps.jailbreak = "data_steward";
  };
  plain = import ../../lib/rename/reconcile.nix { inherit pkgs lib; } {
    reconcileExec = "echo plain";
    pythonBin = "python";
    benchBin = "bench";
    renamedApps = { };
    replacedApps = { };
  };
  devenvSource = builtins.readFile ../../modules/devenv.nix;
in
{
  ironclad-rename-code =
    pkgs.runCommand "ironclad-rename-code-check"
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
        rename() { python3 ${tool} code "$@"; }

        rename --from esign --to esign_webforms --dry-run > ../dry.log
        [ -z "$(git status --porcelain --untracked-files=no)" ] || fail "--dry-run changed the tree"
        grep -q 'rename from esign/public/js/esign.desk.bundle.js' ../dry.log || fail "--dry-run: no bundle rename in the diff"
        grep -q 'rename to esign_webforms/public/js/esign_webforms.desk.bundle.js' ../dry.log || fail "--dry-run: wrong bundle target"
        grep -q '^+app_name = "esign_webforms"' ../dry.log || fail "--dry-run: app_name not in the diff"
        echo "ok   code --dry-run prints the diff and changes nothing"

        echo stray > stray.txt
        if rename --from esign --to esign_webforms 2> ../err; then fail "a dirty tree was renamed"; fi
        grep -q 'uncommitted changes' ../err || fail "dirty tree: $(cat ../err)"
        rm stray.txt
        echo "ok   a dirty tree is refused"

        set +e
        rename --from jailbreak --to data_steward 2> ../err; rc=$?
        set -e
        [ "$rc" = 1 ] && grep -q 'replaced, not renamed' ../err || fail "jailbreak → data_steward: exit $rc, $(cat ../err)"
        echo "ok   code --from jailbreak --to data_steward exits 1 (a replace pair)"

        rename --from esign --to esign_webforms > ../run.log
        cat ../run.log
        [ -d esign_webforms/esign ] && [ ! -e esign ] || fail "the package or its module folder moved wrongly"
        [ "$(cat esign_webforms/modules.txt)" = eSign ] || fail "modules.txt changed"
        for f in public/js/esign_webforms.desk.bundle.js public/js/esign_webforms.control.bundle.js \
                 public/css/esign_webforms.control.bundle.css public/js/web/esign_webforms.web.bundle.js; do
          [ -f "esign_webforms/$f" ] || fail "esign_webforms/$f is missing"
        done
        [ ! -e esign_webforms/public/js/esign.desk.bundle.js ] || fail "esign.desk.bundle.js is still there"
        echo "ok   the OLD.* bundles are NEW.*"

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
        grep -q '"esign_webforms.check()"' esign_webforms/patches.txt || fail "patches.txt execute: line"
        grep -q 'from esign_webforms.esign import' esign_webforms/esign/custom/web_form.py || fail "the import"
        grep -q '"esign_webforms/templates/esign_webforms.html"' esign_webforms/esign/custom/web_form.py || fail "the template path"
        grep -q '/api/method/esign_webforms.esign.custom.web_form.accept' esign_webforms/esign/custom/web_form.py || fail "the /api/method URL"
        grep -q '^import esign_webforms$' esign_webforms/patches/v1/fix_signatures.py || fail "import OLD"
        grep -q 'localStorage.getItem("esign_signature_pad")' esign_webforms/public/js/esign_webforms.desk.bundle.js || fail "a localStorage key changed"
        cmp -s CHANGELOG.md ${../fixtures/rename-esign/CHANGELOG.md} || fail "CHANGELOG.md changed"
        echo "ok   names, dotted paths, patches, imports and URLs rewritten; history and keys kept"

        json=esign_webforms/esign/web_form/esign_demo/esign_demo.json
        grep -q '/assets/esign_webforms/dist/esign-fonts.css' "$json" || fail "the JSON's asset URL"
        ! grep -q '"modified": "2026-01-01 00:00:00.000000"' "$json" || fail "modified not bumped"
        echo "ok   the standard JSON's modified is bumped"

        grep -q '^# ironclad:rename-shim-begin esign$' esign_webforms/hooks.py || fail "no shim"
        grep -q '"esign.esign.custom.web_form.accept": "esign_webforms.esign.custom.web_form.accept",' esign_webforms/hooks.py || fail "shim entry"
        python3 -c 'import ast,sys; ast.parse(open(sys.argv[1]).read())' esign_webforms/hooks.py
        echo "ok   the override_whitelisted_methods shim"

        git commit -qm renamed
        if rename --from esign --to esign_webforms 2> ../err; then fail "a second run renamed something"; fi
        grep -q 'nothing named esign' ../err || fail "second run: $(cat ../err)"
        echo "ok   a second run has nothing to rename"
        touch "$out"
      '';

  ironclad-rename-nixos = pkgs.runCommand "ironclad-rename-nixos-check" { } ''
    set -euo pipefail
    fail() { echo "FAIL $*" >&2; exit 1; }
    export STATE="$PWD/state"
    mkdir -p "$STATE" site/bench/sites "site/sites/${site}"
    echo '{"db_password": "x"}' > "site/sites/${site}/site_config.json"
    sed "s|@SITEDIR@|$PWD/site|g" ${migrateScript} > migrate
    chmod +x migrate
    printf '%s\n' frappe esign jailbreak > "$STATE/installed"

    ./migrate
    cat "$STATE/log"
    line() { grep -nF -- "$1" "$STATE/log" | head -1 | cut -d: -f1; }
    snap="$(line mysqldump)" on="$(line 'set-maintenance-mode on')" ren="$(line 'frappe_rename_app.py')"
    inst="$(line 'install-app data_steward')" uninst="$(line 'uninstall-app jailbreak --yes --no-backup')"
    mig="$(line ' migrate')" off="$(line 'set-maintenance-mode off')"
    for v in snap on ren inst uninst mig off; do [ -n "''${!v}" ] || fail "no $v step"; done
    [ "$snap" -lt "$on" ] && [ "$on" -lt "$ren" ] && [ "$ren" -lt "$inst" ] && [ "$inst" -lt "$uninst" ] \
      && [ "$uninst" -lt "$mig" ] && [ "$mig" -lt "$off" ] || fail "order: snapshot $snap, maintenance $on, rename $ren, install $inst, uninstall $uninst, migrate $mig, off $off"
    grep -q "frappe_rename_app.py --site ${site} --yes esign=esign_webforms (in $PWD/site/bench/sites)" "$STATE/log" \
      || fail "the rename does not run from the bench's sites/"
    [ "$(sort "$STATE/installed" | tr '\n' ' ')" = "data_steward esign frappe " ] || fail "installed: $(cat "$STATE/installed")"
    echo "ok   renamed after maintenance on, data_steward installed and jailbreak uninstalled inside the snapshot, then migrated"

    # The next deploy: the build marker would skip it, so drop it.
    rm -f site/.frappe-migrate-build
    : > "$STATE/log"
    ./migrate
    ! grep -q 'install-app\|uninstall-app' "$STATE/log" || fail "a second run touched the apps: $(cat "$STATE/log")"
    grep -q ' migrate' "$STATE/log" || fail "a second run did not migrate"
    echo "ok   a second run installs and uninstalls nothing"
    cp ${migrateScript} "$out"
  '';

  ironclad-rename-devenv =
    assert lib.assertMsg (
      plain == "echo plain"
    ) "reconcile-apps changes with no renamed or replaced apps";
    assert lib.assertMsg (
      lib.hasInfix "reconcile-apps = scripts.reconcile-apps // {\n                  exec = reconcileAppsExec;" devenvSource
      && lib.hasInfix "\${reconcileAppsExec}" devenvSource
    ) "devenv.nix: reconcile-apps or the frappe:apps-reconcile task does not use the wrapper";
    pkgs.runCommand "ironclad-rename-devenv-check" { } ''
      set -euo pipefail
      fail() { echo "FAIL $*" >&2; exit 1; }
      export STATE="$PWD/state" FRAPPE_BENCH_ROOT="$PWD/bench" FRAPPE_SITE=dev.localhost
      mkdir -p "$STATE" bench/sites/dev.localhost
      printf '%s\n' frappe esign jailbreak > "$STATE/installed"
      cp ${pkgs.writeText "reconcile-apps" reconcile} reconcile-apps

      ${lib.getExe pkgs.bash} reconcile-apps
      cat "$STATE/log"
      line() { grep -nF -- "$1" "$STATE/log" | head -1 | cut -d: -f1; }
      ren="$(line 'frappe_rename_app.py --site dev.localhost --yes esign=esign_webforms')"
      rec="$(line 'reconcile')" inst="$(line 'install-app data_steward')" un="$(line 'uninstall-app jailbreak --yes --no-backup')"
      for v in ren rec inst un; do [ -n "''${!v}" ] || fail "no $v step"; done
      [ "$ren" -lt "$rec" ] && [ "$rec" -lt "$inst" ] && [ "$inst" -lt "$un" ] || fail "order: rename $ren, reconcile $rec, install $inst, uninstall $un"
      grep -q "(in $PWD/bench/sites)" "$STATE/log" || fail "the rename does not run from the bench's sites/"
      echo "ok   reconcile-apps renames first, reconciles, then installs data_steward and uninstalls jailbreak"

      : > "$STATE/log"
      ${lib.getExe pkgs.bash} reconcile-apps
      ! grep -q 'install-app' "$STATE/log" || fail "a second run touched the apps: $(cat "$STATE/log")"
      echo "ok   a second run installs and uninstalls nothing"

      rm -rf bench/sites/dev.localhost
      : > "$STATE/log"
      ${lib.getExe pkgs.bash} reconcile-apps
      [ "$(cat "$STATE/log")" = "reconcile " ] || fail "with no site, it did more than reconcile: $(cat "$STATE/log")"
      echo "ok   with no site yet, only reconcile-apps itself runs"
      touch "$out"
    '';
}
