# What the dev shell says about apps/ on entry — and all it does, since it
# changes nothing.
#
# Entry used to *initialize* any registered submodule it found without a
# checkout (`git submodule update --init`). Entry runs on every `nix develop`
# and every direnv reload, so that was a clone or checkout nobody asked for —
# and how an app taken out by hand, or by the upstream `bench remove-app`
# (which moves apps/<x> into archived/ and leaves .gitmodules alone), came
# straight back, freshly cloned from its remote. Submodules move when you move
# them, or when `bench update --pull` does; this only says what needs doing.
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
    pkgs.gawk
    pkgs.git
    workspaceTool
  ];
  text = ''
    cd "''${1:-.}"
    [ -d apps ] || exit 0

    unchecked=()
    stale=()
    # Unit separator, not tab: tab is IFS whitespace, and `read` collapses an
    # empty field (a submodule with no branch) out of the line.
    while IFS=$'\037' read -r app kind _; do
      case "$kind" in
        submodule-uninitialized)
          # Registered with no checkout is two different things. With a
          # gitlink, the bench records a commit for it and it was just never
          # checked out (a fresh clone) or was deinitialized. Without one the
          # commit is gone and only the .gitmodules entry is left: a removal
          # that stopped halfway. awk reads to the end rather than exiting on
          # the match, so git never takes a SIGPIPE under pipefail.
          if git ls-files -s -- "apps/$app" 2>/dev/null \
            | awk -v p="apps/$app" '$1 == "160000" && $4 == p { f = 1 } END { exit !f }'; then
            unchecked+=("apps/$app")
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

    if [ "''${#unchecked[@]}" -gt 0 ]; then
      echo "frappe-nix: registered but not checked out:$(printf ' %s' "''${unchecked[@]}")" >&2
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
