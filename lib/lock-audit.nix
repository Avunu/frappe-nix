# Is every dependency the workspace declares present in the resolved package set?
#
# An app is a git submodule; moving it to a newer commit can add a requirement
# that `uv.lock` predates. uv2nix then indexes a package set that has no such
# attribute and the bench fails to *evaluate*, with a stack ending in
#
#     error: attribute 'json-repair' missing
#     at …/uv2nix/build/lib/resolvers.nix:123:23
#
# which names neither the app that wants it nor `uv lock`, and lands before any
# shell hook could say so. Asking the same question first turns that into a
# sentence. See lib/python.nix for where the answer is enforced.
#
#   hasPackage : name -> bool — membership in the set resolvers.nix will index.
#                Pass `n: pythonSet ? ${n}`; `?` forces attribute names, not
#                values, so this is as close to free as a check gets.
{ lib }:

{
  workspaceRoot,
  rootPyproject,
  hasPackage,
}:

let
  # PEP 503 normalization, as uv writes names into the lock.
  normalizeName = n: builtins.replaceStrings [ "_" "." ] [ "-" "-" ] (lib.toLower n);

  # PEP 508: the name runs up to the first character that cannot be part of one.
  reqName =
    req:
    let
      m = builtins.match "[[:space:]]*([A-Za-z0-9._-]+).*" req;
    in
    if m == null then null else normalizeName (builtins.head m);

  # Unconditional requirements only. A marker-gated or direct-URL requirement can
  # legitimately sit outside the resolution, and a false alarm here would be
  # worse than the raw uv2nix error it replaces.
  isUnconditional = req: !(lib.hasInfix ";" req) && !(lib.hasInfix "@" req);

  # `members` holds literal paths or globs (`apps/*`).
  expandMember =
    m:
    if lib.hasSuffix "/*" m then
      let
        dir = lib.removeSuffix "/*" m;
        full = workspaceRoot + "/${dir}";
      in
      if builtins.pathExists full then
        map (n: "${dir}/${n}") (
          builtins.attrNames (lib.filterAttrs (_: t: t == "directory") (builtins.readDir full))
        )
      else
        [ ]
    else
      [ m ];

  memberDeps = lib.concatMap (
    dir:
    let
      f = workspaceRoot + "/${dir}/pyproject.toml";
    in
    # A member can be a legacy app with only a setup.py, or a stale entry for a
    # directory that is no longer there. Neither is this check's business.
    if !builtins.pathExists f then
      [ ]
    else
      map (req: {
        where = "${dir}/pyproject.toml";
        inherit req;
      }) ((builtins.fromTOML (builtins.readFile f)).project.dependencies or [ ])
  ) (lib.concatMap expandMember (rootPyproject.tool.uv.workspace.members or [ ]));

  # The root's own deps and dev tools reach the same resolver. `dependency-groups`
  # entries may be `{ include-group = …; }` attrsets rather than requirements.
  rootDeps = map (req: {
    where = "pyproject.toml";
    inherit req;
  }) (
    (rootPyproject.project.dependencies or [ ])
    ++ lib.filter builtins.isString (
      lib.flatten (lib.attrValues (rootPyproject."dependency-groups" or { }))
    )
  );

  missing = lib.filter (d: d.name != null && !(hasPackage d.name)) (
    map (d: d // { name = reqName d.req; }) (
      lib.filter (d: isUnconditional d.req) (memberDeps ++ rootDeps)
    )
  );
in
{
  inherit missing;

  message = ''
    frappe-nix: uv.lock does not cover every declared dependency, so the Python
    environment cannot be resolved.

    ${lib.concatMapStringsSep "\n" (
      d: "  ${d.where} requires ${d.req}, but '${d.name}' is not in uv.lock"
    ) missing}

    This is what an app bump looks like: a submodule moved to a commit whose
    pyproject.toml declares something new, and uv.lock was not regenerated.
    Re-lock the workspace from the bench root:

        uv lock

    This error blocks the dev shell, so `uv` is not on PATH unless you are
    already in one. Without it:

        nix run .#relock
  '';
}
