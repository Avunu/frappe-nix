# frappe-nix-node-locks — writes node-locks/<target>/yarn.lock, the fallback
# lock for a node target that ships no yarn.lock of its own.
#
# The rule is that an app's node_modules is built from the app's yarn.lock
# (lib/yarn-lock.nix, lib/bench.nix), and nothing about it is committed to the
# bench. This tool exists for the exceptions. An app whose upstream never
# committed a lock gets one resolved here from its package.json — `yarn
# install` in a scratch directory holding nothing but the manifests, and the
# yarn.lock it writes is the fallback — so that the build has something to pin
# to. An app whose upstream lock is broken (a dependency bump that never
# regenerated the transitive entries, leaving a range no entry satisfies —
# yarn's "Couldn't find any versions for … in our cache" offline) can be
# *forced*: named explicitly, it gets a fallback resolved with its own
# yarn.lock as the seed, so upstream's pins stay and only the gap is filled,
# and the stamp records that the override was deliberate. The build then takes
# the fallback over the upstream lock for that target, and only that one.
#
# Committed, because the sandbox has no network and a bench's apps are
# submodules whose trees are pinned — the lock cannot live next to the app's
# package.json. The stamp (source.json) is what keeps regeneration cheap and
# honest: a target whose manifests have not moved costs nothing, one whose
# manifests have is re-resolved, and the previous fallback is the seed so only
# what has to move does.
#
# yarn is taken from PATH on purpose: the dev shell pins its own nodejs/yarn
# via `frappe-nix.nodejs`, and `relock` puts the same on PATH, so the lock is
# resolved by the yarn that will install it.
{ pkgs }:

pkgs.writeShellApplication {
  name = "frappe-nix-node-locks";
  runtimeInputs = with pkgs; [
    coreutils
    findutils
    gawk
    jq
  ];
  text = ''
    usage() {
      echo "usage: frappe-nix-node-locks [--exclude=<app/subdir>]... [--command=<how to invoke me>] <bench-root> <locks-dir> [<target>...]" >&2
      echo "  no targets: every app and nested frontend under <bench-root>/apps without a yarn.lock" >&2
      echo "  of its own (plus the forced locks already in <locks-dir>), and <locks-dir> is pruned" >&2
      echo "  to the targets that exist; with targets: exactly those, and one that ships a yarn.lock" >&2
      echo "  gets a forced lock that overrides it" >&2
      exit 2
    }

    excludes=()
    command="bench-update --node-locks"
    while [ "$#" -gt 0 ]; do
      case "$1" in
        --exclude=*) excludes+=("''${1#--exclude=}"); shift ;;
        --exclude) excludes+=("$2"); shift 2 ;;
        --command=*) command="''${1#--command=}"; shift ;;
        -h|--help) usage ;;
        --) shift; break ;;
        -*) echo "frappe-nix-node-locks: unknown flag $1" >&2; usage ;;
        *) break ;;
      esac
    done
    [ "$#" -ge 2 ] || usage

    bench="$1"
    locks="$2"
    shift 2
    cd "$bench"
    # A relative locks dir is relative to the bench root, like everything else here.
    case "$locks" in /*) : ;; *) locks="$PWD/$locks" ;; esac
    label="''${locks##*/}"

    _excluded() {
      local e
      for e in "''${excludes[@]}"; do [ "$e" = "$1" ] && return 0; done
      return 1
    }

    # `path = <subdir>` lines of the app's own .gitmodules: a subdirectory the
    # app tracks as a submodule is a project of its own, not its frontend.
    _submodule_paths() {
      [ -f "apps/$1/.gitmodules" ] || return 0
      sed -nE 's/^[[:space:]]*path[[:space:]]*=[[:space:]]*([^[:space:]]+)[[:space:]]*$/\1/p' "apps/$1/.gitmodules"
    }

    # The same rule as lib/node-targets.nix, over the tree as it is on disk.
    _discover() {
      local pj spj app sub
      for pj in apps/*/package.json; do
        [ -e "$pj" ] || continue
        app="''${pj#apps/}"; app="''${app%/package.json}"
        printf '%s\n' "$app"
        for spj in "apps/$app"/*/package.json; do
          [ -e "$spj" ] || continue
          sub="''${spj#apps/"$app"/}"; sub="''${sub%/package.json}"
          [ "$sub" = node_modules ] && continue
          _submodule_paths "$app" | grep -qxF -- "$sub" && continue
          _excluded "$app/$sub" && continue
          printf '%s/%s\n' "$app" "$sub"
        done
      done
    }

    # What the fallback was resolved from, hashed, and whether it was forced
    # over an upstream lock. lib/bench.nix reads the same keys to warn about a
    # fallback older than its manifests.
    _stamp() { # <app dir> <forced>
      (
        cd "$1"
        jq -n \
          --arg p "$(sha256sum package.json | cut -d' ' -f1)" \
          --arg y "$( { [ -f yarn.lock ] && sha256sum yarn.lock | cut -d' ' -f1; } || true )" \
          --argjson f "$2" \
          '{"package.json": $p}
           + (if $y == "" then {} else {"yarn.lock": $y} end)
           + (if $f then {forced: true} else {} end)'
      )
    }

    _is_forced() { # <target> — the committed stamp's word
      [ -f "$locks/$1/source.json" ] && [ "$(jq -r '.forced // false' "$locks/$1/source.json")" = true ]
    }

    # One line per entry the build could not fetch — the same rules
    # lib/yarn-lock.nix throws on, checked here where the fix is cheap.
    # shellcheck disable=SC2016
    VALIDATE='
      function flush() {
        if (key == "") return
        if (key ~ /@(file|link):/) print key ": a file:/link: dependency, which the Nix build cannot fetch"
        else if (resolved == "") print key ": no resolved URL"
        else if (resolved ~ /^(git\+|git:|ssh:)/ || resolved ~ /\.git(#.*)?$/ || resolved ~ /^https:\/\/codeload\.github\.com\//) {
          if (resolved !~ /#[0-9a-f]+$/ && resolved !~ /codeload\.github\.com\/[^\/]+\/[^\/]+\/tar\.gz\/[0-9a-f]+$/)
            print key ": a git dependency without a commit: " resolved
        }
        else if (resolved ~ /^https:\/\// && !integ && resolved !~ /#/) print key ": no integrity and no #hash on " resolved
        key = ""
      }
      /^[^ \t#].*:[ \t]*$/ { flush(); key = $0; sub(/:[ \t]*$/, "", key); resolved = ""; integ = 0; next }
      /^  resolved / { resolved = $2; gsub(/"/, "", resolved); next }
      /^  integrity / { integ = 1; next }
      END { flush() }
    '

    failed=()

    _generate() { # <target> <forced>
      local key="$1" forced="$2" src dst want work bad
      src="apps/$key"
      dst="$locks/$key"
      if [ ! -f "$src/package.json" ]; then
        echo "  ✗ $key: no apps/$key/package.json" >&2
        failed+=("$key")
        return
      fi

      want="$(_stamp "$src" "$forced")"
      if [ -f "$dst/yarn.lock" ] && [ -f "$dst/source.json" ] \
         && [ "$(jq -cS . "$dst/source.json")" = "$(printf '%s' "$want" | jq -cS .)" ]; then
        return
      fi

      work="$(mktemp -d)"
      # cp -L: in app mode the apps are symlink mirrors of their flake inputs.
      cp -L "$src/package.json" "$work/package.json"
      for f in .yarnrc .npmrc; do
        [ -f "$src/$f" ] && cp -L "$src/$f" "$work/$f"
      done
      # The seed: upstream's own lock when forcing (its pins stay, the gap is
      # what gets resolved), else the previous fallback (only what must move
      # does). yarn keeps every entry the manifest still wants.
      if $forced && [ -f "$src/yarn.lock" ]; then
        # `--no-preserve=mode`: apps/<key> may be a store path (app mode), whose
        # files are 0444, and yarn has to rewrite the seed it is handed
        cp -L --no-preserve=mode "$src/yarn.lock" "$work/yarn.lock"
        echo "  resolving $key (forced: seeded from apps/$key/yarn.lock, gaps filled from the registry)…"
      elif [ -f "$dst/yarn.lock" ]; then
        cp "$dst/yarn.lock" "$work/yarn.lock"
        echo "  resolving $key (from package.json, seeded from the previous $label/$key/yarn.lock)…"
      else
        echo "  resolving $key (from package.json — no yarn.lock upstream)…"
      fi

      # Everything installs into the scratch directory and is thrown away;
      # the lock is the product. NODE_ENV=production would drop
      # devDependencies, which is where every vite frontend keeps its build
      # tooling. The same script/engine/platform flags as the sandbox install,
      # so what resolves here is what installs there.
      if ! (cd "$work" && env -u NODE_ENV yarn install --ignore-scripts --ignore-engines --ignore-platform \
              --production=false --non-interactive --no-progress) > "$work/yarn.log" 2>&1; then
        echo "  ✗ $key: yarn could not resolve a lock:" >&2
        grep -E '^(error|warning)' "$work/yarn.log" | tail -n 20 >&2
        failed+=("$key")
        rm -rf "$work"
        return
      fi
      if [ ! -f "$work/yarn.lock" ]; then
        echo "  ✗ $key: yarn install wrote no yarn.lock" >&2
        failed+=("$key")
        rm -rf "$work"
        return
      fi

      bad="$(awk "$VALIDATE" "$work/yarn.lock")"
      if [ -n "$bad" ]; then
        echo "  ✗ $key: entries the Nix build cannot fetch:" >&2
        printf '     %s\n' "$bad" >&2
        failed+=("$key")
        rm -rf "$work"
        return
      fi

      mkdir -p "$dst"
      # Leftovers of the npm-based scheme this replaced.
      rm -f "$dst/package-lock.json" "$dst/package.json"
      install -m 0644 "$work/yarn.lock" "$dst/yarn.lock"
      # Last: an interrupted run is retried next time, not remembered as done.
      printf '%s\n' "$want" | jq -S . > "$dst/source.json"
      rm -rf "$work"
      if $forced; then
        echo "  + $label/$key (forced over apps/$key/yarn.lock)"
      else
        echo "  + $label/$key"
      fi
    }

    if [ "$#" -gt 0 ]; then
      # Named: resolve it, and over the app's own lock if it has one — that
      # is the only way a forced lock comes to be.
      for key in "$@"; do
        if [ -f "apps/$key/yarn.lock" ]; then _generate "$key" true; else _generate "$key" false; fi
      done
    else
      mapfile -t targets < <(_discover)
      for key in "''${targets[@]}"; do
        dst="$locks/$key"
        if [ ! -f "apps/$key/yarn.lock" ]; then
          _generate "$key" false
        elif _is_forced "$key"; then
          _generate "$key" true
        elif [ -f "$dst/yarn.lock" ]; then
          echo "  ~ $label/$key is unused — apps/$key ships a yarn.lock and builds from it; git rm -r $label/$key, or force it: $command $key"
        elif [ -f "$dst/package-lock.json" ] || [ -f "$dst/package.json" ] || [ -f "$dst/source.json" ]; then
          rm -f "$dst/package-lock.json" "$dst/package.json" "$dst/source.json"
          echo "  - $label/$key (npm-based lock; apps/$key builds from its own yarn.lock now)"
        fi
      done

      if [ -d "$locks" ]; then
        while IFS= read -r dir; do
          rel="''${dir#"$locks"/}"
          keep=false
          for key in "''${targets[@]}"; do [ "$key" = "$rel" ] && keep=true; done
          if ! $keep; then
            rm -rf "$dir"
            echo "  - $label/$rel (no longer a target)"
          fi
        done < <(find "$locks" -mindepth 1 \( -name yarn.lock -o -name package-lock.json \) -printf '%h\n' | sort -u)
        find "$locks" -mindepth 1 -type d -empty -delete
      fi
    fi

    if [ "''${#failed[@]}" -gt 0 ]; then
      echo "node locks NOT regenerated for: ''${failed[*]}" >&2
      exit 1
    fi
  '';
}
