# `nix run .#relock`: regenerates what a repository commits on frappe-nix's
# behalf. In a bench, uv.lock in the bench root (run from there). In app mode,
# <lockDir>/uv.lock from a writable copy of the workspace the flake assembles,
# and <lockDir>/node-locks/ for the pins without a yarn.lock, both staged.
#
# A flake app, deliberately outside every other output's dependency graph: it
# has to evaluate when nothing that touches the Python workspace can, which is
# exactly when it is needed (a stale or missing lock fails evaluation).
{
  pkgs,
  lib ? pkgs.lib,
  appMode,
  # The uv, nodejs and yarn the locks are resolved with: the dev shell's own,
  # so a fallback is resolved by the yarn that will install it.
  uv ? pkgs.uv,
  nodejs ? pkgs.nodejs,
  yarn ? pkgs.yarn,
  # lib/node-locks.nix's frappe-nix-node-locks (app mode).
  nodeLocksTool ? null,
  # The nested frontends the lock generator must leave alone.
  nodeNestedFrontendExcludes ? [ ],
  # lib/build-deps.nix's sync, after the lock (a bench); null runs nothing.
  buildDepsSyncBin ? null,
  # App mode: the assembled workspace without its lock, and where the
  # repository keeps the locks (frappe-nix.app.lockDir).
  workspace ? null,
  lockDir ? "nix",
}:

let
  lockRel = "${lockDir}/uv.lock";
  locksRel = "${lockDir}/node-locks";
  # The nested frontends the lock generator must leave alone, as flags.
  nodeLocksExcludeFlags = lib.escapeShellArgs (map (k: "--exclude=${k}") nodeNestedFrontendExcludes);
in
pkgs.writeShellApplication {
  name = "frappe-nix-relock";
  # cfg.nodejs and yarn: the lock generator takes them from PATH so a
  # fallback is resolved by the yarn that will install it.
  runtimeInputs = [
    uv
  ]
  ++ lib.optionals appMode [
    pkgs.git
    nodejs
    yarn
    nodeLocksTool
  ];
  text =
    if !appMode then
      ''
        if [ ! -f pyproject.toml ] || [ ! -d apps ]; then
          echo "frappe-nix-relock: run this from the bench root (pyproject.toml + apps/)" >&2
          exit 1
        fi
        echo "Re-locking the Python workspace with $(uv --version)…"
        uv lock "$@"
        ${lib.optionalString (buildDepsSyncBin != null) buildDepsSyncBin}
        echo "✅ uv.lock updated — commit it (and pyproject.toml), then re-enter the shell."
      ''
    else
      ''
        DO_UV=true
        DO_LOCKS=true
        declare -a UV_ARGS=()
        declare -a LOCK_TARGETS=()
        for arg in "$@"; do
          case "$arg" in
            --uv-only) DO_LOCKS=false ;;
            --node-locks) DO_UV=false ;;
            --node-hashes)
              echo "relock: --node-hashes is now --node-locks (node-offline-hashes.json was replaced by node-locks/)" >&2
              DO_UV=false ;;
            -h | --help)
              cat <<'EOF'
        Usage: nix run .#relock [--uv-only | --node-locks [<app>[/<subdir>]…]] [uv lock flags…]

        Regenerates what this app repo commits on frappe-nix's behalf:

          ${lockRel}     the resolved Python workspace
          ${locksRel}/   a fallback yarn.lock for each pinned app and nested
                         frontend that ships none of its own (the ones that
                         do build from it, and nothing is written for them).
                         Name a target after --node-locks to force a lock
                         over the yarn.lock it ships — for one that cannot
                         resolve offline.

        Run it after `nix flake update` and after editing pyproject.toml.
        Both are staged for you: a flake's source tree is only its tracked
        files, so an unstaged lock is still invisible to evaluation.
        EOF
              exit 0
              ;;
            -*) UV_ARGS+=("$arg") ;;
            *) if $DO_UV; then UV_ARGS+=("$arg"); else LOCK_TARGETS+=("$arg"); fi ;;
          esac
        done

        ROOT="$(git rev-parse --show-toplevel 2>/dev/null || echo "$PWD")"
        LOCKDIR="$ROOT/${lockDir}"
        mkdir -p "$LOCKDIR"

        if $DO_UV; then
          # A writable copy of the workspace this flake assembles, minus
          # uv.lock. Everything uv reads — the generated root
          # pyproject.toml, every apps/*/pyproject.toml — is already
          # exactly what evaluation will see, so what resolves here is
          # what the build would have resolved.
          #
          # The result travels: uv.lock records workspace members by
          # relative path (`source = { editable = "apps/frappe" }`) and
          # carries no hash for them, so a lock produced in a temp
          # directory is usable verbatim from the store workspace.
          WORK="$(mktemp -d)"
          trap 'chmod -R u+w "$WORK" 2>/dev/null || true; rm -rf "$WORK"' EXIT
          cp -a ${workspace}/. "$WORK/"
          chmod -R u+w "$WORK"

          # Seed the previous resolution so uv only moves what has to
          # move; without it every relock is a fresh resolution and the
          # diff is the whole file.
          if [ -f "$LOCKDIR/uv.lock" ]; then
            install -m 0644 "$LOCKDIR/uv.lock" "$WORK/uv.lock"
          fi

          export UV_CACHE_DIR="''${UV_CACHE_DIR:-''${XDG_CACHE_HOME:-$HOME/.cache}/uv}"
          echo "Re-locking the Python workspace with $(uv --version)…"
          (cd "$WORK" && uv lock "''${UV_ARGS[@]}")

          if [ -f "$LOCKDIR/uv.lock" ] && cmp -s "$WORK/uv.lock" "$LOCKDIR/uv.lock"; then
            echo "  ${lockRel} already current"
          else
            install -m 0644 "$WORK/uv.lock" "$LOCKDIR/uv.lock"
            echo "  + ${lockRel}"
          fi
        fi

        if $DO_LOCKS; then
          # Over the assembled workspace: its apps/ are the pinned
          # inputs, mirrored as symlinks the generator reads through.
          echo "── Refreshing ${locksRel} (fallback locks for pins without a yarn.lock) ──"
          frappe-nix-node-locks ${nodeLocksExcludeFlags} --command='nix run .#relock -- --node-locks' \
            ${workspace} "$LOCKDIR/node-locks" "''${LOCK_TARGETS[@]}"
        fi

        if [ -f "$LOCKDIR/uv.lock" ] &&
          ! git -C "$ROOT" ls-files --error-unmatch -- "${lockRel}" > /dev/null 2>&1; then
          git -C "$ROOT" add -- "${lockRel}" &&
            echo "  staged ${lockRel} (untracked, it is invisible to the flake)"
        fi
        if [ -d "$LOCKDIR/node-locks" ] &&
          [ -n "$(git -C "$ROOT" ls-files --others --exclude-standard -- "${locksRel}")" ]; then
          git -C "$ROOT" add -- "${locksRel}" &&
            echo "  staged new files under ${locksRel}/ (untracked, they are invisible to the flake)"
        fi

        echo "✅ commit ${lockDir}/, then re-enter the shell."
      '';
}
