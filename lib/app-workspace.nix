# Assembles a bench workspace in the store, for a repo that is one Frappe app
# rather than a whole bench.
#
# A bench repo *is* the uv workspace: pyproject.toml and apps/ are tracked files
# and every consumer (uv2nix, lib/lock-audit.nix) reads them off the checkout. An
# app repo has neither, so this builds the same shape out of flake inputs —
# `apps/<name>` per pinned app, the app under development, a generated root
# pyproject.toml, and the committed uv.lock — and hands the result to those
# consumers as `workspaceRoot`. Nothing downstream needs to know which mode
# produced it.
#
# The apps are MIRRORED, not copied: `cp -rs` reproduces every directory for real
# and symlinks every file back to the input it came from.
#
# Not copied, because this directory is forced during *evaluation* — uv2nix's
# loadWorkspace reads it, so does the lock audit — and it depends on the app's own
# source, so it is rebuilt whenever that moves, i.e. on every commit. A copy of
# frappe plus erpnext is 300 MB re-hashed each time; a mirror is a few thousand
# links.
#
# And not plain `ln -s` per app either, though that would be smaller still: uv2nix
# builds each workspace member from its directory, so `apps/<x>` becomes a
# derivation `src`, and a symlink there reaches stdenv as "do not know how to
# unpack source archive". Real directories of symlinked files unpack fine —
# `cp -pr` copies the links and `chmod -R` does not follow them — and every Frappe
# app builds with flit_core, which only reads its sources.
#
# The links inside are still not usable from a sandbox: a path added to the store
# as a *source* keeps no references, so the inputs they point at are not inputs of
# anything that copies this directory. modules/devenv.nix therefore points each
# member's `src` back at the input it was mirrored from (see `srcOverrides` in
# lib/python.nix), and lib/bench.nix takes `appSrcs` for the same reason. What is
# left here is what uv2nix and the lock audit *read* at evaluation time, where
# following a symlink is free.
#
# The root pyproject.toml is rendered from templates/bench/pyproject.toml with
# the same placeholders lib/sh/template.sh substitutes, and the members and
# sources tables are filled by the same `frappe-nix-workspace` the scaffolder
# uses. That is deliberate: a second generator would drift, and computing
# [tool.uv.sources] keys means reading each app's own [project].name — which is
# what uv resolves the member to, and which is not always the directory name. A
# directory-named key is inert where the two differ, and uv quietly resolves the
# app from PyPI instead.

{
  pkgs,
  lib,
  # Ordered [{ name; src; }] — frappe first, the app under development last.
  # Ordered, not an attrset, because this becomes sites/apps.txt and therefore
  # the order provision-site installs in: erpnext has to land before hrms, and
  # Nix sorts attribute names.
  apps,
  # [project].name for the workspace root. uv refuses a root whose name collides
  # with a member's distribution name, and in app mode it always would — the
  # bench is named after the app — so callers pass a suffixed name.
  projectName,
  # An entry of lib/frappe-presets.json.
  preset,
  # The app repo's committed uv.lock, or null. Null still evaluates: `nix run
  # .#relock` has to be reachable in a repo that does not have a lock yet, and
  # it only needs the apps and the generated pyproject.toml.
  lockFile ? null,
  # The app repo's committed node-offline-hashes.json, or null. Copied to where
  # lib/bench.nix already looks for it, so that code path needs no app-mode case.
  nodeHashesFile ? null,
}:

let
  workspaceTool = import ./workspace-tool.nix { inherit pkgs; };

  appNames = map (a: a.name) apps;

  # An app with no pyproject.toml (a legacy setup.py-only app) stays in apps/ and
  # on PYTHONPATH but cannot be a uv member — the same policy compute_membership
  # applies in lib/sh/apps.sh.
  memberNames = map (a: a.name) (
    lib.filter (a: builtins.pathExists (a.src + "/pyproject.toml")) apps
  );

  # "python314" -> "py314" / "3.14", exactly as resolve_preset does in
  # lib/sh/common.sh.
  pynum = lib.removePrefix "python" preset.python;
  pytag = "py${pynum}";
  pyver = "${builtins.substring 0 1 pynum}.${builtins.substring 1 (-1) pynum}";

  # A TOML array of strings is a JSON array of strings, so this needs no renderer
  # of its own. It is substituted before anything parses the file:
  # `override-dependencies = @OVERRIDES@` is the one placeholder in the template
  # that is not itself valid TOML.
  overrides = builtins.toJSON preset.overrideDependencies;
in

# runCommandLocal, not runCommand: this is read during evaluation, so it must
# never queue behind a remote builder or a binary-cache round trip.
pkgs.runCommandLocal "frappe-app-workspace-${projectName}"
  {
    nativeBuildInputs = [ workspaceTool ];
    passthru = {
      inherit appNames;
      appSrcs = lib.listToAttrs (map (a: lib.nameValuePair a.name a.src) apps);
    };
  }
  ''
    mkdir -p "$out/apps" "$out/sites"

    # Interpolation, not `toString`: `toString` on a path yields the store path
    # with its *context stripped*, so the source never becomes an input of this
    # derivation and every link into it dangles inside the sandbox. Which does not
    # fail loudly — `frappe-nix-workspace sync-apps` reads each app's
    # pyproject.toml through these links, and a link it cannot follow makes it
    # fall back to the directory name for the [tool.uv.sources] key. That key is
    # then inert and uv resolves the app from PyPI instead.
    ${lib.concatMapStrings (a: ''
      cp -rs ${lib.escapeShellArg "${a.src}"} "$out/apps/${a.name}"
    '') apps}
    # The mirrored directories come out read-only, and sync-apps has nothing to
    # write inside them — but uv2nix hands them to stdenv, which wants to be able
    # to. -R walks physically, so this never follows the file links.
    chmod -R u+w "$out/apps"

    substitute ${../templates/bench/pyproject.toml} "$out/pyproject.toml" \
      --replace-fail '@PROJECT_NAME@'    ${lib.escapeShellArg projectName} \
      --replace-fail '@REQUIRES_PYTHON@' ${lib.escapeShellArg preset.requiresPython} \
      --replace-fail '@OVERRIDES@'       ${lib.escapeShellArg overrides} \
      --replace-fail '@PYTAG@'           ${lib.escapeShellArg pytag} \
      --replace-fail '@PYVER@'           ${lib.escapeShellArg pyver}

    cd "$out"
    # The same two calls reconcile_workspace makes (lib/sh/apps.sh). sync-apps
    # keys [tool.uv.sources] on each app's own distribution name; apps-txt hoists
    # frappe first on its own, so a `frappe` declared out of order still lands
    # where bench needs it.
    frappe-nix-workspace sync-apps --pyproject pyproject.toml ${lib.escapeShellArgs memberNames}
    frappe-nix-workspace apps-txt --file sites/apps.txt --add ${lib.escapeShellArgs appNames}

    ${lib.optionalString (lockFile != null) ''
      install -m 0644 ${lockFile} "$out/uv.lock"
    ''}
    ${lib.optionalString (nodeHashesFile != null) ''
      install -m 0644 ${nodeHashesFile} "$out/node-offline-hashes.json"
    ''}
  ''
