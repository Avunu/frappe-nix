# What the dev shell does about apps/ on entry: check out, once, the app
# submodules a fresh clone of the bench has never had, and only report on the
# rest.
#
# Once per clone, not on every entry. Entry runs on every `nix develop` and
# every direnv reload, and it used to run `git submodule update --init` on any
# registered submodule it found without a checkout — which is how an app
# removed by hand, or deinitialized, came straight back, freshly cloned from
# its remote. So the checkout is limited to a submodule this clone has never
# set up (see ever_set_up); one that has been set up and is now missing was
# taken out by someone, and entry leaves it out and says how to bring it back.
# Submodules otherwise move when you move them, or when `bench update --pull`
# does.
#
# First, before anything else on entry. The rest of the hook works in apps/<x>
# from what Nix saw, and with `self.submodules = true` Nix fetches every
# submodule itself, checked out or not: the node_modules install once ran
# `yarn install` in each still-empty app directory and left node_modules/
# behind, and git will not clone into a directory that is not empty.
#
# One path at a time and not --recursive: a single `git submodule update` over
# several paths gives up on all of them when one clone fails, and Frappe apps
# ship nested submodules with broken refs that have no role in production.
#
# The clone this makes is a partial one, not the shallow one `shallow = true`
# in .gitmodules asks for. A shallow `git submodule update` clones the remote's
# *default* branch at depth 1 and then fetches the pinned commit — usually on
# another branch — with no depth at all, so it is slow and still leaves the
# clone following one branch that is not even the app's. Instead:
#
#   - the branch .gitmodules pairs the app with is cloned with its whole
#     history of commits and folders, but no file contents
#     (--filter=blob:none): `git log`, `git log -- <file>` and merge-bases work
#     offline, and a checkout downloads only the files it needs;
#   - every other branch is fetched the same way, so `git switch <any branch>`
#     works and downloads only the files it needs.
#
# Not commits-only (tree:0) for those other branches, which is what this once
# did. A treeless clone has no folders for any commit, so everything that
# walks history — `git log -p` or `git log -- <path>` in an editor's history
# view, `git blame`, `git diff` across a range — fetches them one commit at a
# time, each as a pack of its own with the folders the last one already had
# repeated in it. Measured on a 12k-object repo, a handful of such commands
# left 345 packs holding 1.9x the objects there were.
#
# Two more things keep a partial clone from growing without bound, because
# git does neither:
#
#   - the pinned commit is fetched, the ordinary way, before anything can ask
#     git for a commit the clone lacks (prefetch_pin). `git submodule update`
#     to a pin the clone has not got first asks the clone for it, and a
#     partial clone answers with a lazy fetch that negotiates nothing — no
#     "have" lines — so the remote sends the commit's whole history over
#     again. One of those is a clone's worth of data: for erpnext, 1.5 GB per
#     pin bump, thirty times in a week, 48 GB in all. Measured, the same
#     update adds nothing to a full clone and 206 objects to a shallow one,
#     but 11,800 to a partial clone of 11,400 objects (blobless or treeless
#     alike); with the pin fetched first it adds 27;
#   - the clone's packs are merged where they are alike in size (tidy_packs).
#     Every lazy fetch is a pack, and the `git maintenance run --auto` that a
#     fetch starts does not merge them in a partial clone: 383 packs stayed
#     383, where a geometric repack takes them to 2 with the same objects. Not
#     `git gc --auto`, which does the same but counts packs and not bytes, and
#     so leaves a few giant duplicates alone until there are fifty.
#
# An app already checked out shallow, or following a single branch — every
# clone made before this — is brought to the same shape in place, once: the
# checkout does not move, only history is fetched. See needs_history. A clone
# an earlier frappe-nix made treeless keeps the commits it has without their
# folders, and gets folders for the ones it fetches from now on (see
# stop_treeless); `git fetch --refetch` in the app fills in the rest, at the
# price of downloading its history again.
#
# Every one of these clones is set never to recurse into submodules on fetch.
# An app with submodules of its own (hrms has frontend/frappe-ui) otherwise
# has git diff each commit a fetch brings in, looking for a moved gitlink, and
# in a treeless clone each of those trees is a lazy fetch from the remote: one
# round trip per commit on any other branch. hrms's conversion made thousands,
# for most of an hour. Those nested submodules are never checked out here
# (see above), so the check has nothing to find. See no_submodule_fetch.
#
# The classifier, not `git submodule status`, names what each apps/<x> is:
# the latter dies on the first gitlink with no .gitmodules entry, which is one
# of the shapes this exists to report.
{ pkgs }:

let
  workspaceTool = import ./workspace-tool.nix { inherit pkgs; };
in
pkgs.writeShellApplication {
  name = "frappe-nix-apps-report";
  runtimeInputs = [
    pkgs.coreutils
    pkgs.findutils
    pkgs.gawk
    pkgs.git
    workspaceTool
  ];
  text = ''
    cd "''${1:-.}"
    [ -d apps ] || exit 0

    # The name git keeps a submodule's clone and config under: its .gitmodules
    # section, which bench-get-app makes the path but nothing requires to be.
    # awk reads to the end rather than exiting on the match, so git never takes
    # a SIGPIPE under pipefail.
    name_of() { # <path>
      git config -f .gitmodules --get-regexp '^submodule\..*\.path$' 2>/dev/null \
        | awk -v p="$1" '$2 == p && n == "" { n = $1; sub(/^submodule\./, "", n); sub(/\.path$/, "", n) } END { print n }' \
        || true
    }

    # Whether this clone has ever set the submodule up: a clone of it under the
    # git dir (what `git submodule update --init` and bench-get-app make), or a
    # URL in the local config (what `git submodule init` writes, and all that
    # `git submodule add` over an existing checkout leaves — frappe-init
    # --migrate, whose apps keep their .git in apps/<x>). A fresh clone of the
    # bench has neither for any app. An app removed by hand keeps both, and a
    # deinitialized one keeps its clone.
    ever_set_up() { # <name>
      [ -e "$(git rev-parse --git-path "modules/$1")" ] \
        || git config --get "submodule.$1.url" > /dev/null 2>&1
    }

    has_gitlink() { # <path>
      git ls-files -s -- "$1" 2>/dev/null \
        | awk -v p="$1" '$1 == "160000" && $4 == p { f = 1 } END { exit !f }'
    }

    # The branch .gitmodules pairs the app with. Empty, or git's "." (the
    # superproject's branch, which means nothing for an app), is the remote's
    # default.
    branch_of() { # <name>
      local b
      b="$(git config -f .gitmodules --get "submodule.$1.branch" || true)"
      [ "$b" != "." ] || b=""
      printf '%s' "$b"
    }

    is_shallow() { # <path>
      [ "$(git -C "$1" rev-parse --is-shallow-repository 2>/dev/null)" = true ]
    }

    # A treeless clone's own fetches (yours, an editor's autofetch, the next
    # one here) skip the on-demand submodule check — see the header. In the
    # clone's config, and only where it says nothing of its own, so a value
    # someone set there by hand stands.
    no_submodule_fetch() { # <path>
      git -C "$1" config --local --get fetch.recurseSubmodules > /dev/null 2>&1 \
        || git -C "$1" config fetch.recurseSubmodules false
    }

    # Every branch, with its folders but no file contents, from here on. The
    # refspec goes into the config only once the fetch has worked, so a clone
    # the fetch failed on still reads as needing it (see needs_history) and the
    # next entry retries.
    track_all_branches() { # <path>
      local args=(--filter=blob:none --no-recurse-submodules)
      if is_shallow "$1"; then args+=(--unshallow); fi
      git -C "$1" fetch -q "''${args[@]}" origin '+refs/heads/*:refs/remotes/origin/*' < /dev/null || return 1
      git -C "$1" config --replace-all remote.origin.fetch '+refs/heads/*:refs/remotes/origin/*'
      git -C "$1" config remote.origin.partialclonefilter blob:none
      no_submodule_fetch "$1"
    }

    is_partial() { # <path>
      [ "$(git -C "$1" config --get remote.origin.promisor 2>/dev/null)" = true ]
    }

    # A clone an earlier frappe-nix made treeless fetches folders along with
    # commits from now on (see the header). Config only: nothing is downloaded
    # here, and the commits it already has stay as they are. A filter someone
    # set there by hand to anything but tree:0 stands.
    stop_treeless() { # <path>
      [ "$(git -C "$1" config --get remote.origin.partialclonefilter 2>/dev/null)" = tree:0 ] || return 0
      git -C "$1" config remote.origin.partialclonefilter blob:none
    }

    # The commit the bench records for an app.
    pinned_commit() { # <path>
      git ls-files -s -- "$1" 2>/dev/null \
        | awk -v p="$1" '$1 == "160000" && $4 == p { print $2 }'
    }

    # Whether a commit is on disk, without the lazy fetch a partial clone would
    # make to answer the question — which is the whole reason to ask.
    have_commit() { # <path> <commit>
      GIT_NO_LAZY_FETCH=1 git -C "$1" cat-file -e "$2^{commit}" 2> /dev/null
    }

    # Fetch the commit the bench records, the ordinary way, when the clone lacks
    # it; see the header for what asking a partial clone for it costs
    # otherwise. An ordinary fetch negotiates, so what comes down is the commits
    # since the clone's last one. Asked on every entry, because a pin moves
    # under a clone with a `git pull` of the bench, which changes nothing the
    # shell watches and so does not re-enter it; the next entry is the first
    # chance to fetch it before `git submodule update` is run. A fetch that
    # fails (offline, a pin the remote no longer has) is reported and leaves
    # the clone as it was.
    prefetch_pin() { # <path>
      local pin
      pin="$(pinned_commit "$1")"
      [ -n "$pin" ] || return 0
      have_commit "$1" "$pin" && return 0
      timeout 300 git -C "$1" fetch -q --no-recurse-submodules origin "$pin" < /dev/null > /dev/null 2>&1 \
        || echo "frappe-nix: could not fetch ''${1}'s pinned commit ''${pin:0:12} — 'git submodule update' on it will download the app's whole history again" >&2
    }

    # Merge a partial clone's packs where they are alike in size. git's own
    # background maintenance does not (see the header), and `gc --auto` counts
    # packs, not bytes: five repeats of a 1.5 GB download are five packs, well
    # under its limit of 50, and 7.5 GB. A geometric repack keeps each pack at
    # least twice the size of the next smaller, so packs of one size — what a
    # repeated download is — are merged, and the objects they share are stored
    # once; when they already are in that shape it does nothing, so asking on
    # every entry costs a directory listing. More than one pack to look at, or
    # not at all.
    tidy_packs() { # <path>
      local packs
      packs="$(find "$(git -C "$1" rev-parse --git-path objects/pack)" -name '*.pack' 2> /dev/null | wc -l)"
      [ "$packs" -gt 1 ] || return 0
      git -C "$1" maintenance run --quiet --task=geometric-repack < /dev/null > /dev/null 2>&1 || true
    }

    # The clone the first entry makes (see the header), at the pinned commit.
    # git's own submodule clone cannot pick the branch — it always takes the
    # remote's default — so this clones by hand and then hands the result to
    # git: absorbgitdirs moves it under .git/modules/<name> as `git submodule
    # update` would have put it, and `update --force` checks out the pin (it
    # otherwise skips a submodule whose HEAD is already the pinned commit,
    # checked out or not).
    first_checkout() { # <path> <name>
      local url branch args=()
      git submodule init -q -- "$1" || return 1
      url="$(git config --get "submodule.$2.url")" || return 1
      branch="$(branch_of "$2")"
      if [ -n "$branch" ]; then args=(--branch "$branch"); fi
      if ! git clone -q --no-checkout --filter=blob:none --single-branch "''${args[@]}" -- "$url" "$1" < /dev/null; then
        [ -n "$branch" ] || return 1
        echo "frappe-nix: could not clone $1 at '$branch' (.gitmodules' branch) — trying its remote's default branch" >&2
        git clone -q --no-checkout --filter=blob:none --single-branch -- "$url" "$1" < /dev/null || return 1
      fi
      git submodule absorbgitdirs -- "$1" > /dev/null || return 1
      # The pin is usually on another branch than the one just cloned, and
      # `submodule update` asks the clone for it before it fetches anything.
      prefetch_pin "$1"
      git submodule update --force -- "$1" < /dev/null >&2 || return 1
      track_all_branches "$1" || echo "frappe-nix: $1 is checked out, but its other branches could not be fetched; the next shell entry tries again" >&2
    }

    # Put things back as they were before first_checkout — which, since it only
    # ever runs for an app this clone has never set up, is: no clone under the
    # git dir, no URL in the config, and an empty directory. So the next entry
    # still counts the app as new and tries again, rather than as one someone
    # took out.
    undo_first_checkout() { # <path> <name> <submodule.<name>.active before>
      local gitdir
      gitdir="$(git rev-parse --git-path "modules/$2")"
      case "$2" in
        *..*) ;;
        *) rm -rf "$gitdir" ;;
      esac
      if [ -d "$1" ]; then find "$1" -mindepth 1 -delete 2>/dev/null || true; fi
      git config --unset "submodule.$2.url" || true
      [ -n "$3" ] || git config --unset "submodule.$2.active" || true
    }

    # A checked-out app that is shallow, or follows one branch of origin: what
    # `shallow = true` made of every app until the first entry cloned them as
    # above. An app with no origin, or one whose origin already fetches every
    # branch (a full clone, bench-get-app's), is left as it is.
    needs_history() { # <path>
      local specs
      is_shallow "$1" && return 0
      specs="$(git -C "$1" config --get-all remote.origin.fetch 2>/dev/null || true)"
      [ -n "$specs" ] || return 1
      [[ "$specs" != *'refs/heads/*:'* ]]
    }

    # needs_history's app, in place, to first_checkout's shape: the paired
    # branch's commits and folders, every branch's commits. HEAD, the checkout
    # and any local branch stay exactly where they are; this only fetches.
    add_history() { # <path> <name>
      local branch args=()
      branch="$(branch_of "$2")"
      if [ -n "$branch" ]; then
        if is_shallow "$1"; then args+=(--unshallow); fi
        git -C "$1" fetch -q "''${args[@]}" --filter=blob:none --no-recurse-submodules origin \
          "+refs/heads/$branch:refs/remotes/origin/$branch" < /dev/null \
          || echo "frappe-nix: could not fetch $1's '$branch' history — fetching every branch's commits alone" >&2
      fi
      track_all_branches "$1"
    }

    first=()
    checked=()
    unchecked=()
    stale=()
    # Unit separator, not tab: tab is IFS whitespace, and `read` collapses an
    # empty field (a submodule with no branch) out of the line.
    while IFS=$'\037' read -r app kind _; do
      case "$kind" in
        submodule)
          checked+=("$app")
          ;;
        submodule-uninitialized)
          # Registered with no checkout is two different things. With a
          # gitlink, the bench records a commit for it and it was never checked
          # out (a fresh clone) or was taken out since. Without one the commit
          # is gone and only the .gitmodules entry is left: a removal that
          # stopped halfway.
          if has_gitlink "apps/$app"; then
            name="$(name_of "apps/$app")"
            if [ -n "$name" ] && ! ever_set_up "$name"; then
              first+=("$app")
            else
              unchecked+=("apps/$app")
            fi
          else
            stale+=("$app")
          fi
          ;;
        nested-repo)
          echo "frappe-nix: apps/$app is a git repository but not a registered submodule —" >&2
          echo "  'nix build' will not see it. Vendor it (frappe-init --migrate) or push it" >&2
          echo "  and re-add it with bench-get-app; see README, 'Local apps'." >&2
          ;;
      esac
    done < <(frappe-nix-workspace apps --apps-dir apps 2>/dev/null | tr '\t' '\037' || true)

    failed=()
    for app in "''${first[@]}"; do
      # Git refuses to clone into a directory with anything in it. Say what is
      # in the way rather than set the submodule up for a clone that fails.
      if [ -n "$(find "apps/$app" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]; then
        echo "frappe-nix: apps/$app is not checked out, but its directory is not empty, so git" >&2
        echo "  will not clone into it. Move what is there aside and re-enter the shell:" >&2
        find "apps/$app" -mindepth 1 -maxdepth 1 -printf '    %f\n' | sort >&2
        continue
      fi
      name="$(name_of "apps/$app")"
      active_before="$(git config --get "submodule.$name.active" || true)"
      echo "frappe-nix: checking out apps/$app (first shell entry in this clone)" >&2
      if first_checkout "apps/$app" "$name"; then
        continue
      fi
      failed+=("apps/$app")
      undo_first_checkout "apps/$app" "$name" "$active_before"
    done

    for app in "''${checked[@]}"; do
      if is_partial "apps/$app"; then
        # A clone made treeless by a frappe-nix from before no_submodule_fetch,
        # or from before folders were fetched for every branch.
        no_submodule_fetch "apps/$app"
        stop_treeless "apps/$app"
        prefetch_pin "apps/$app"
        tidy_packs "apps/$app"
      fi
      needs_history "apps/$app" || continue
      name="$(name_of "apps/$app")"
      echo "frappe-nix: apps/$app is a shallow or single-branch clone — fetching its branch's history" >&2
      echo "  (commits and folders, no file contents) and every branch's, once. This can take a while." >&2
      add_history "apps/$app" "$name" \
        || echo "frappe-nix: could not fetch apps/$app's history — the next shell entry tries again" >&2
    done

    if [ "''${#failed[@]}" -gt 0 ]; then
      echo "frappe-nix: could not check out:$(printf ' %s' "''${failed[@]}") — see git's error above." >&2
      echo "  The next shell entry tries again, or: git submodule update --init --$(printf ' %s' "''${failed[@]}")" >&2
    fi
    if [ "''${#unchecked[@]}" -gt 0 ]; then
      echo "frappe-nix: registered but not checked out:$(printf ' %s' "''${unchecked[@]}")" >&2
      echo "  (this clone has set them up before, so the shell does not bring them back)" >&2
      echo "  at the commits the bench records:  git submodule update --init --$(printf ' %s' "''${unchecked[@]}")" >&2
      echo "  or at their branches' tips:        bench update --pull" >&2
    fi
    for app in "''${stale[@]}"; do
      echo "frappe-nix: .gitmodules still registers apps/$app, but the bench records no commit" >&2
      echo "  for it — a removal that stopped halfway. Finish it: bench remove-app $app" >&2
    done
    exit 0
  '';
}
