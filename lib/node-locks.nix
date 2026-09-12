# frappe-nix-node-locks — writes node-locks/<target>/ for every node target of a
# bench: the package-lock.json the Nix build installs node_modules from, the
# normalized package.json it was resolved against, and a stamp of the sources.
#
# The lock is what makes the build hash-free. `pkgs.importNpmLock` fetches each
# package by the `integrity` the lock already carries, so nothing has to be
# mined out of a failing fixed-output derivation the way fetchYarnDeps' mirror
# hash had to be. And npm is the one resolving it: with a yarn.lock beside the
# manifest, `npm install --package-lock-only` pins to the yarn.lock's versions
# and integrity (it refuses a yarn.lock whose hash is wrong, so it really does
# read it); without one, it resolves from package.json. Those are the two
# things a Frappe app can ship, and one command covers both.
#
# Committed, because the sandbox has no network and a bench's apps are
# submodules whose trees are pinned — the lock cannot live next to the app's
# package.json. The stamp is what keeps regeneration cheap and honest: a target
# whose manifests have not moved costs nothing, and a target whose manifests
# have is regenerated whether or not a lock exists.
#
# npm is taken from PATH on purpose: the dev shell and `relock` put
# `frappe-nix.nodejs` there, and a lock resolved by that npm is the lock that
# nodejs's npm installs in the sandbox.
{ pkgs }:

pkgs.writeShellApplication {
  name = "frappe-nix-node-locks";
  runtimeInputs = with pkgs; [
    coreutils
    findutils
    jq
  ];
  text = ''
    usage() {
      echo "usage: frappe-nix-node-locks [--exclude=<app/subdir>]... <bench-root> <locks-dir> [<target>...]" >&2
      echo "  no targets: discover every app and nested frontend under <bench-root>/apps," >&2
      echo "  and prune <locks-dir> to that set; with targets: only those, no pruning" >&2
      exit 2
    }

    excludes=()
    while [ "$#" -gt 0 ]; do
      case "$1" in
        --exclude=*) excludes+=("''${1#--exclude=}"); shift ;;
        --exclude) excludes+=("$2"); shift 2 ;;
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

    # The three manifests npm resolves from, hashed. Keys follow presence, so a
    # yarn.lock appearing or disappearing is a change too.
    _stamp() {
      (
        cd "$1"
        jq -n \
          --arg p "$(sha256sum package.json | cut -d' ' -f1)" \
          --arg y "$( { [ -f yarn.lock ] && sha256sum yarn.lock | cut -d' ' -f1; } || true )" \
          --arg l "$( { [ -f package-lock.json ] && sha256sum package-lock.json | cut -d' ' -f1; } || true )" \
          '{"package.json": $p}
           + (if $y == "" then {} else {"yarn.lock": $y} end)
           + (if $l == "" then {} else {"package-lock.json": $l} end)'
      )
    }

    # What npm resolves from, and what the Nix build later installs from —
    # the same file, so the two can never disagree. yarn's `resolutions`
    # become npm's `overrides` ("a/b" nests, "@s/p/c" keeps the scope
    # together, "**/x" is just x); an explicit `overrides` wins. `workspaces`
    # goes: the workspace members are other targets with locks of their own.
    # shellcheck disable=SC2016  # jq programs: the $names are jq's, not bash's
    NORMALIZE='
      def override_path:
        split("/") | reduce .[] as $seg ([];
          if $seg == "**" then .
          elif (length > 0 and (.[-1] | startswith("@")) and ((.[-1] | contains("/")) | not))
            then .[:-1] + [.[-1] + "/" + $seg]
          else . + [$seg] end);
      def set_override($path; $v):
        if ($path | length) == 1 then
          if (.[$path[0]] | type) == "object" then .[$path[0]]["."] = $v else .[$path[0]] = $v end
        else
          .[$path[0]] = (if (.[$path[0]] | type) == "string" then {".": .[$path[0]]} else (.[$path[0]] // {}) end)
          | .[$path[0]] |= set_override($path[1:]; $v)
        end;
      ([ (.resolutions // {}) | to_entries[] | {path: (.key | override_path), value} | select(.path != []) ]
         | reduce .[] as $e ({}; set_override($e.path; $e.value))) as $translated
      | .overrides = ($translated * (.overrides // {}))
      | (if .overrides == {} then del(.overrides) else . end)
      | del(.resolutions) | del(.workspaces)
    '

    # Git dependencies need two repairs before fetchGit can take them. npm
    # records a GitHub one as git+ssh://git@github.com/…, whatever the manifest
    # said — that would need an SSH key in the sandbox, the https spelling
    # needs nothing. And when npm imports the entry from a yarn.lock it keeps
    # the repository but drops the commit, which fetchGit would then have to
    # resolve impurely; the yarn.lock has the commit, so it is put back from
    # there ($pins: repository URL, without .git → commit).
    # shellcheck disable=SC2016
    POSTPROCESS='
      .packages |= with_entries(.value |=
        (if ((.resolved // "") | startswith("git+")) then
           (.resolved | sub("^git\\+ssh://git@github\\.com/"; "git+https://github.com/")) as $r
           | (if ($r | contains("#")) then $r
              else (($r | sub("\\.git$"; "")) as $u | if $pins[$u] then $r + "#" + $pins[$u] else $r end)
              end) as $pinned
           | .resolved = $pinned
         else . end))
    '

    # One line per entry the build could not fetch: a link: dependency, a
    # resolved value with no scheme (a file: path), a registry tarball with no
    # integrity to fetch it by, or a git dependency with no commit to check
    # out. Empty output means the lock is buildable.
    VALIDATE='
      .packages | to_entries[] | select(.key != "") | select((.value.inBundle // false) | not)
      | select( (.value.link // false)
             or (((.value.resolved // "") | test("^[a-z+]+://")) | not)
             or (((.value.resolved // "") | test("^https?://")) and ((.value.integrity // "") == ""))
             or (((.value.resolved // "") | startswith("git+")) and (((.value.resolved // "") | contains("#")) | not)) )
      | "\(.key): resolved=\(.value.resolved // "-") integrity=\(.value.integrity // "-")"
    '

    # The commits a yarn.lock pins its git dependencies to, as JSON keyed by
    # repository URL (https spelling, no .git). Empty object without a lock.
    _git_pins() {
      if [ -f "$1" ]; then
        sed -nE 's/^[[:space:]]*resolved "(git\+[a-z]+:\/\/[^#"]+)#([0-9a-f]{7,40})"[[:space:]]*$/\1 \2/p' "$1" \
          | sed -E 's#^git\+ssh://git@github\.com/#git+https://github.com/#; s/\.git ([0-9a-f]+)$/ \1/' \
          | jq -Rn '[inputs | select(length > 0) | split(" ") | {key: .[0], value: .[1]}] | from_entries'
      else
        echo '{}'
      fi
    }

    prune=false
    targets=()
    if [ "$#" -gt 0 ]; then
      targets=("$@")
    else
      prune=true
      mapfile -t targets < <(_discover)
    fi

    failed=()
    for key in "''${targets[@]}"; do
      src="apps/$key"
      dst="$locks/$key"
      if [ ! -f "$src/package.json" ]; then
        echo "  ✗ $key: no apps/$key/package.json" >&2
        failed+=("$key")
        continue
      fi

      want="$(_stamp "$src")"
      if [ -f "$dst/package-lock.json" ] && [ -f "$dst/package.json" ] && [ -f "$dst/source.json" ] \
         && [ "$(jq -cS . "$dst/source.json")" = "$(printf '%s' "$want" | jq -cS .)" ]; then
        continue
      fi

      work="$(mktemp -d)"
      # cp -L: in app mode the apps are symlink mirrors of their flake inputs.
      jq "$NORMALIZE" "$src/package.json" > "$work/package.json"
      [ -f "$src/yarn.lock" ] && cp -L "$src/yarn.lock" "$work/yarn.lock"
      [ -f "$src/package-lock.json" ] && cp -L "$src/package-lock.json" "$work/package-lock.json"

      if [ -f "$src/yarn.lock" ]; then
        echo "  resolving $key (pinned by its yarn.lock)…"
      else
        echo "  resolving $key (from package.json — no yarn.lock)…"
      fi
      # --legacy-peer-deps: yarn v1 never auto-installed peers, and the tree
      # upstream tested is the one without them. The build installs with the
      # same flag; the two must agree or the offline install re-resolves.
      # NODE_ENV=production would drop devDependencies, which is where every
      # vite frontend keeps its build tooling.
      if ! (cd "$work" && env -u NODE_ENV npm install --package-lock-only --ignore-scripts \
              --legacy-peer-deps --no-audit --no-fund --no-progress --loglevel=error) \
            > "$work/npm.log" 2>&1; then
        echo "  ✗ $key: npm could not resolve a lock:" >&2
        tail -n 20 "$work/npm.log" >&2
        failed+=("$key")
        rm -rf "$work"
        continue
      fi

      # The app's own yarn.lock, not the copy: npm rewrites the copy in its
      # own dialect, commit gone.
      jq --argjson pins "$(_git_pins "$src/yarn.lock")" "$POSTPROCESS" "$work/package-lock.json" > "$work/lock.json"
      bad="$(jq -r "$VALIDATE" "$work/lock.json")"
      if [ -n "$bad" ]; then
        echo "  ✗ $key: entries the Nix build cannot fetch (link:/file: dependencies, or no integrity):" >&2
        printf '     %s\n' "$bad" >&2
        failed+=("$key")
        rm -rf "$work"
        continue
      fi

      mkdir -p "$dst"
      install -m 0644 "$work/package.json" "$dst/package.json"
      install -m 0644 "$work/lock.json" "$dst/package-lock.json"
      # Last: an interrupted run is retried next time, not remembered as done.
      printf '%s\n' "$want" | jq -S . > "$dst/source.json"
      rm -rf "$work"
      echo "  + ''${locks##*/}/$key"
    done

    if $prune && [ -d "$locks" ]; then
      while IFS= read -r dir; do
        rel="''${dir#"$locks"/}"
        keep=false
        for key in "''${targets[@]}"; do [ "$key" = "$rel" ] && keep=true; done
        if ! $keep; then
          rm -rf "$dir"
          echo "  - ''${locks##*/}/$rel (no longer a target)"
        fi
      done < <(find "$locks" -mindepth 1 -name package-lock.json -printf '%h\n' | sort)
      find "$locks" -mindepth 1 -type d -empty -delete
    fi

    if [ "''${#failed[@]}" -gt 0 ]; then
      echo "node locks NOT regenerated for: ''${failed[*]}" >&2
      exit 1
    fi
  '';
}
