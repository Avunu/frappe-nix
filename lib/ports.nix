# A dev bench's TCP ports (docs/ironclad/spec.md §5.12), as pure functions so
# tests/ironclad/runtime.nix can call them with any checkout path and any
# environment. modules/devenv.nix calls them with `builtins.getEnv`.
#
# Only nginx (the web port) and Mailpit bind TCP in a socket-mode bench; MariaDB
# and Redis are on sockets in $DEVENV_RUNTIME. Every port is a fixed base plus
# one offset, 0..899, decided at evaluation: devenv's port allocator is the
# identity under its flake integration (src/modules/processes.nix falls back to
# `base` when the native CLI's primop is absent), so nothing moves a port that
# is already taken. Two benches that hash to the same offset collide, and the
# offset is what keeps them apart.
{ lib }:

rec {
  # sha256(seed)[0:4] mod 900. With the bench name as the seed this is the offset
  # every bench has had since ports were hashed: the primary checkout's ports
  # stay where they were.
  offsetFor =
    seed: lib.mod (lib.fromHexString (builtins.substring 0 4 (builtins.hashString "sha256" seed))) 900;

  # What the offset is hashed from.
  #
  # The bench name, so every clone of a bench lands on the same ports (in bench
  # mode the web port is written into the committed common_site_config.json, so a
  # path-derived one would dirty every clone). In app mode a *linked worktree* of
  # the app (`git worktree add`: its .git is a file, not a directory) adds its
  # path, so two worktrees of one app can run at once: the generated bench, and
  # with it common_site_config.json, is per checkout and never committed.
  #
  #   gitKind  what `<pwd>/.git` is: "regular", "directory", or null (absent,
  #            or pure evaluation, where PWD is "")
  seed =
    {
      benchName,
      appMode,
      pwd,
      gitKind,
    }:
    if appMode && pwd != "" && gitKind == "regular" then "${benchName}@${pwd}" else benchName;

  # FRAPPE_NIX_PORT_OFFSET as a number, or null when unset. A value that is not
  # 0..899 is ignored with a warning rather than failing evaluation: the shell
  # still opens, on the ports it would have had.
  parseEnvOffset =
    value:
    if value == "" then
      null
    else if builtins.match "[0-9]{1,3}" value != null && lib.toIntBase10 value <= 899 then
      lib.toIntBase10 value
    else
      lib.warn "frappe-nix: FRAPPE_NIX_PORT_OFFSET=${value} is not a number from 0 to 899; ignored" null;

  # The offset in force: the environment's when set, else the option's.
  effectiveOffset = envOffset: optionOffset: if envOffset != null then envOffset else optionOffset;

  # Every base, from one offset. `web` is nginx's (or, with sockets off, the web
  # server's); `db` names MariaDB's allocated but unbound port under sockets;
  # the three Mailpit ports are the devguard.mail defaults.
  basesFor = offset: {
    web = 8000 + offset;
    db = 3306 + offset;
    socketio = 9000 + offset;
    mailSmtp = 19000 + offset;
    mailHttp = 20000 + offset;
    mailPop3 = 21000 + offset;
  };

  # `<pwd>/.git`'s kind, for `seed`. Impure by nature (it reads the checkout),
  # so modules/devenv.nix calls it and the tests pass `gitKind` directly.
  gitKindAt =
    pwd:
    let
      dotGit = pwd + "/.git";
    in
    if pwd == "" || !(builtins.pathExists dotGit) then null else builtins.readFileType dotGit;
}
