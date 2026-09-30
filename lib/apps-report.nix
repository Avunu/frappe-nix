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

    first=()
    unchecked=()
    stale=()
    # Unit separator, not tab: tab is IFS whitespace, and `read` collapses an
    # empty field (a submodule with no branch) out of the line.
    while IFS=$'\037' read -r app kind _; do
      case "$kind" in
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
      if git submodule update --init -- "apps/$app" < /dev/null >&2; then
        continue
      fi
      failed+=("apps/$app")
      # `--init` records the URL before it clones. With no clone to show for
      # it, take that back, so the submodule still reads as never set up and the
      # next entry tries again — rather than as one someone took out.
      if [ ! -e "$(git rev-parse --git-path "modules/$name")" ]; then
        git config --unset "submodule.$name.url" || true
        [ -n "$active_before" ] || git config --unset "submodule.$name.active" || true
      fi
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
