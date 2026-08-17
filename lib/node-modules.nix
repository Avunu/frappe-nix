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
    findutils
  ];
  text = ''
    if [ "$#" -lt 2 ]; then
      echo "usage: frappe-nix-node-modules <bench-root> <app>..." >&2
      exit 2
    fi

    cd "$1"
    shift

    # Every manifest `yarn install` reads, hashed together with its path so a
    # nested frontend appearing or disappearing counts as a change too.
    # node_modules is pruned: it holds thousands of package.json files, all of
    # them outputs of the very install this is deciding whether to run. .git is
    # pruned because nothing under it is an input to yarn.
    _fingerprint() {
      find "apps/$1" \( -name node_modules -o -name .git \) -prune -o \
        \( -name package.json -o -name yarn.lock \) -print0 \
        | LC_ALL=C sort -z \
        | xargs -0 -r sha256sum \
        | sha256sum \
        | cut -d' ' -f1
    }

    _failed=()

    for app in "$@"; do
      nm="apps/$app/node_modules"

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
      if (cd "apps/$app" && yarn install --frozen-lockfile) > "$log" 2>&1; then
        # Re-read rather than reuse $want: a postinstall (patch-package, a
        # nested `yarn install`) can rewrite a manifest, and recording the
        # pre-install value would make every later run reinstall from scratch.
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

    if [ ''${#_failed[@]} -gt 0 ]; then
      echo "" >&2
      echo "node_modules is out of date for:" >&2
      printf '  %s\n' "''${_failed[@]}" >&2
      echo "Fix the yarn error above — anything built until then is built against" >&2
      echo "the previous install." >&2
      exit 1
    fi
  '';
}
