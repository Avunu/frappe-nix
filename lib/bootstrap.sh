# <label>-bootstrap [ROOT]: a fresh clone's submodules, checked out before Nix
# evaluates the project's flake.
#
# A project's flake sets `inputs.self.submodules = true`, so Nix reads every
# submodule while it evaluates the flake: from its working-tree path when the
# tree is dirty (a fresh clone has none there, and the evaluation fails), or
# fetched by Nix itself, one full clone per submodule, when it is clean. Both
# happen before the dev shell exists, so the checkout shell entry makes
# (`<label>-sources enter`) never gets to run. The project's .envrc runs this
# first, when `git submodule status` lists one that is not checked out.
#
# It runs `<label>-sources enter` -- the dev shell's own command, so the clones
# come out in the shape shell entry gives them -- from the product revision
# the project's flake.lock pins, read without evaluating the project's flake.
# A lock that pins none, or a revision from before the product exposed the
# command, gets the one this script was built with. Quiet when there is
# nothing to do; exits 0 on everything but bad usage, like `enter` itself, so a
# .envrc that runs it never fails because of it.
#
# Set before this file by lib/shell-tools.nix: LABEL, INPUT (the project's
# input naming the product), SOURCES (the bundled <label>-sources), LOCKREF
# (lib/bootstrap-lock.nix), STATUS (the `git submodule status` arguments that
# list the submodules the command checks out) and TREELESS (a submodule path
# cloned commits-only, when the project has it; empty for none).

case "${1:-}" in
  -h | --help)
    echo "usage: $LABEL-bootstrap [ROOT]"
    echo "  Check out a fresh clone's submodules with $LABEL-sources, at the revision"
    echo "  ROOT/flake.lock pins, before Nix evaluates the flake. ROOT defaults to ."
    exit 0
    ;;
esac
if [ "$#" -gt 1 ]; then
  echo "usage: $LABEL-bootstrap [ROOT]" >&2
  exit 2
fi

root="$(cd "${1:-.}" 2> /dev/null && pwd)" || exit 0
cd "$root" || exit 0
[ "$(git rev-parse --is-inside-work-tree 2> /dev/null)" = true ] || exit 0
# shellcheck disable=SC2086 # STATUS is a list of arguments
git submodule status $STATUS 2> /dev/null | grep '^-' > /dev/null || exit 0

args=()
if [ -n "$TREELESS" ] &&
  git config -f .gitmodules --get-regexp '^submodule\..*\.path$' 2> /dev/null |
  cut -d' ' -f2- | grep -xF -- "$TREELESS" > /dev/null; then
  args=(--treeless "$TREELESS")
fi

tool="$SOURCES"
if [ -f flake.lock ] &&
  ref="$(BOOTSTRAP_LOCK="$root/flake.lock" nix eval --raw --impure --expr \
    "import $LOCKREF { lock = builtins.getEnv \"BOOTSTRAP_LOCK\"; input = \"$INPUT\"; }" 2> /dev/null)" &&
  nix eval --raw "$ref#sources.outPath" > /dev/null 2>&1; then
  echo "$LABEL: checking out submodules before Nix evaluates the flake, with $LABEL-sources from $ref" >&2
  if out="$(nix build --no-link --print-out-paths "$ref#sources")"; then
    tool="$out/bin/$LABEL-sources"
  else
    echo "$LABEL: could not build it; using $LABEL-bootstrap's own" >&2
  fi
else
  echo "$LABEL: checking out submodules before Nix evaluates the flake (flake.lock pins no $LABEL with a sources command; using $LABEL-bootstrap's own)" >&2
fi

"$tool" enter "${args[@]}" "$root" || true
