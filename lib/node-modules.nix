# Keeps each app's mutable dev `node_modules` in step with the app's manifests.
#
# The dev shell installs node_modules per app with a plain `yarn install`,
# outside Nix, because a nested frontend's postinstall needs the network the
# sandbox does not have. That install has to be skipped once it is done — a
# `yarn install` per app on every shell entry costs minutes — and the obvious
# sentinel, a bare `touch node_modules/.frappe-nix-installed`, answers the wrong
# question: it records *that* an install happened, never *what* it installed.
#
# So the first `bench update` that pulls an app whose package.json gained a
# dependency leaves the sentinel in place, the shell keeps skipping, and the
# build dies on a package nothing ever fetched:
#
#     Cannot find package '@framework/ui' imported from
#     apps/helpdesk/desk/vite.config.js
#
# — which is not a build error the message leads you to diagnose as a stale
# node_modules, and which no amount of re-entering the shell repairs.
#
# The sentinel here holds a fingerprint of every manifest the install reads —
# the app's own package.json/yarn.lock and each nested frontend's — so a pull
# that changes any of them is what triggers the reinstall.
#
# yarn is taken from PATH on purpose: the dev shell pins its own nodejs/yarn via
# `frappe-nix.nodejs`, and a yarn baked in here would shadow the pinned one.
{ pkgs }:

pkgs.writeShellApplication {
  name = "frappe-nix-node-modules";
  runtimeInputs = with pkgs; [
    coreutils
    diffutils
    findutils
  ];
  text = ''
    if [ "$#" -lt 2 ]; then
      echo "usage: frappe-nix-node-modules <bench-root> <app>..." >&2
      exit 2
    fi

    cd "$1"
    shift

    # What every walk of an app below skips. node_modules is pruned: it holds
    # thousands of package.json files, all of them outputs of the very install
    # this is deciding whether to run. .git is pruned because nothing under it
    # is an input to yarn.
    #
    # And the extra prunes are what -H (below) makes necessary: the app-mode
    # bench is materialised *inside* the repository, so descending through the
    # symlink reaches a full copy of frappe at
    # `apps/<app>/.frappe-nix/bench/apps/frappe` and would count the framework's
    # manifests as if they were the app's.
    _prune=(
      \( -name node_modules -o -name .git
         -o -name .frappe-nix -o -name .devenv -o -name .direnv \) -prune -o
    )

    # Every manifest `yarn install` reads, hashed together with its path so a
    # nested frontend appearing or disappearing counts as a change too.
    #
    # -H, because in app mode `apps/<the app under development>` is a symlink to
    # the repository, and find's default -P mode prints a symlinked start point
    # and does not descend. That would fingerprint *nothing* — and sha256sum of
    # an empty stream is a constant, so the sentinel would match forever and the
    # install would be skipped no matter what package.json did. Which is exactly
    # the silent staleness this whole file exists to prevent. -H follows the
    # argument only, so symlinks *inside* an app are still not traversed.
    _fingerprint() {
      find -H "apps/$1" "''${_prune[@]}" \
        \( -name package.json -o -name yarn.lock \) -print0 \
        | LC_ALL=C sort -z \
        | xargs -0 -r sha256sum \
        | sha256sum \
        | cut -d' ' -f1
    }

    # The lockfiles under an app, nested frontends included — the one thing the
    # install below is not meant to leave different from how it found it.
    _lockfiles() {
      find -H "apps/$1" "''${_prune[@]}" \
        \( -name yarn.lock -o -name package-lock.json \) -print0
    }

    # An app's lockfiles belong to the app's repository, not to this bench. The
    # root install below is frozen and never writes one, but the postinstall of
    # most apps runs a non-frozen `yarn install` in each nested frontend — and
    # that rewrites a tracked lock (whatever is stale or unformatted upstream),
    # or writes one where upstream ships none. Upstream-owned and regenerable,
    # but dirty: the app's next `git checkout` then refuses over it, and
    # `bench update` dies on an app it has nothing to do with.
    #
    # There is no switch to stop the nested installs: yarn 1 takes
    # --pure-lockfile from the command line or an ancestor's .yarnrc only, and a
    # .yarnrc would stop the lock of an app being developed being written too.
    # So the install is bracketed: copy the locks first, put back whatever it
    # changed. A lock that was already edited is in the copy, so it comes back
    # edited; only yarn's own rewrite is undone. Hash-free and git-free on
    # purpose, so it behaves the same for an app that is not a repository.
    _snapshot_locks() { # <app> <dir>
      local f
      mkdir -p "$2/apps/$1"
      while IFS= read -r -d "" f; do
        mkdir -p "$2/$(dirname "$f")"
        cp -p -- "$f" "$2/$f"
      done < <(_lockfiles "$1")
    }

    _restore_locks() { # <app> <dir>
      local f
      # Written or rewritten by the install.
      while IFS= read -r -d "" f; do
        if [ ! -e "$2/$f" ]; then
          rm -f -- "$f"
          echo "  · $f: created by the install, removed"
        elif ! cmp -s -- "$f" "$2/$f"; then
          cp -pf -- "$2/$f" "$f"
          echo "  · $f: rewritten by the install, put back"
        fi
      done < <(_lockfiles "$1")
      # Deleted by it.
      while IFS= read -r -d "" f; do
        if [ ! -e "$f" ]; then
          mkdir -p "$(dirname "$f")"
          cp -pf -- "$2/$f" "$f"
          echo "  · $f: deleted by the install, put back"
        fi
      done < <(cd "$2" && find "apps/$1" -type f -print0)
    }

    _failed=()
    _absent=()

    for app in "$@"; do
      nm="apps/$app/node_modules"

      # The app list is what Nix saw, and with `self.submodules = true` Nix
      # fetches every submodule itself, checked out or not. In an app that is
      # not checked out, `yarn install` finds no package.json, succeeds having
      # done nothing, and leaves node_modules/ behind: a directory git will then
      # not clone the app into, and a sentinel that fingerprints nothing.
      if [ ! -f "apps/$app/package.json" ]; then
        _absent+=("$app")
        continue
      fi

      # An earlier frappe-nix symlinked the Nix-built node_modules here. Those
      # are built with --ignore-scripts and are read-only, so nested frontends
      # have no deps and nothing can be edited — replace it with a real install.
      case "$(readlink "$nm" 2>/dev/null || true)" in
        /nix/store/*)
          echo "Replacing Nix store node_modules symlink for $app..."
          rm "$nm"
          ;;
      esac

      want=$(_fingerprint "$app")
      if [ "$(cat "$nm/.frappe-nix-installed" 2>/dev/null || true)" = "$want" ]; then
        continue
      fi

      if ! command -v yarn > /dev/null 2>&1; then
        echo "frappe-nix-node-modules: yarn is not on PATH — cannot install $app" >&2
        exit 2
      fi

      echo "Installing node_modules for $app (incl. nested frontends)..."
      log=$(mktemp)
      snap=$(mktemp -d)
      _snapshot_locks "$app" "$snap"
      _installed=true
      (cd "apps/$app" && yarn install --frozen-lockfile) > "$log" 2>&1 || _installed=false
      # Whether or not it worked: a failed install can have rewritten a lock too.
      _restore_locks "$app" "$snap"
      rm -rf "$snap"
      if $_installed; then
        # Re-read rather than reuse $want: a postinstall (patch-package) can
        # rewrite a manifest, and recording the pre-install value would make
        # every later run reinstall from scratch. After the restore above, so
        # the locks it puts back are what is recorded, not what yarn wrote.
        mkdir -p "$nm"
        _fingerprint "$app" > "$nm/.frappe-nix-installed"
        echo "  ✓ $app"
      else
        echo "  ⚠  yarn install failed for $app — node_modules left as it was:" >&2
        tail -20 "$log" >&2
        _failed+=("$app")
      fi
      rm -f "$log"
    done

    if [ ''${#_absent[@]} -gt 0 ]; then
      echo "node_modules not installed for apps with no package.json on disk (not checked out?):" >&2
      printf '  apps/%s\n' "''${_absent[@]}" >&2
    fi

    if [ ''${#_failed[@]} -gt 0 ]; then
      echo "" >&2
      echo "node_modules is out of date for:" >&2
      printf '  %s\n' "''${_failed[@]}" >&2
      echo "Fix the yarn error above — anything built until then is built against" >&2
      echo "the previous install." >&2
      exit 1
    fi

    # Not an install failure, but not an install either: `bench build` must not
    # go on as if these apps had their node_modules.
    [ ''${#_absent[@]} -eq 0 ] || exit 1
  '';
}
