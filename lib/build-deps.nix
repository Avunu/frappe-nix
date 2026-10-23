# [tool.uv.extra-build-dependencies], derived from uv.lock rather than kept by
# hand (python-devops-nix's lib.python.buildDeps).
#
# uv2nix builds a package the lock has no wheel for from its sdist, in the
# sandbox, with only the build dependencies it is told about, and an old sdist
# often declares none. Every such package gets setuptools; `always` names what
# the lock cannot show. The entries sit between two sentinel lines in the
# bench's pyproject.toml:
#
#   [tool.uv.extra-build-dependencies]
#   # >>> frappe-nix: sdist build deps (managed) >>>
#   "docopt" = ["setuptools"]
#   # <<< frappe-nix: sdist build deps (managed) <<<
#
# and `frappe-nix-build-deps update` rewrites them after every `uv lock` (the
# lock itself does not record them, so it does not need redoing). A bench from
# before the block keeps its hand-kept list, untouched, until it adds the two
# sentinel lines (docs/development/locks.md).
#
#   extra  a bench's own entries (frappe-nix.pythonBuildDependencies), merged
#          over frappe-nix's
{
  pkgs,
  python-devops-nix,
  extra ? { },
}:
let
  inherit (pkgs) lib;

  label = "frappe-nix";

  # Listed whatever the lock says, in place of the derived entry.
  always = {
    # uv builds without isolation here, so a package's own build-system.requires
    # is not enough: the backend is named again. A git source in a bench, so the
    # lock has no wheel for it and the derived entry would say setuptools.
    frappe-runtime = [ "hatchling" ];
    # A cffi wrapper whose setup.py imports cffi; sdist-only on some locks.
    cairocffi = [
      "setuptools"
      "cffi"
      "pycparser"
    ];
  };

  mk =
    entries:
    python-devops-nix.lib.python.buildDeps {
      inherit pkgs label;
      always = entries;
    };

  command = mk (always // extra);

  begin = "# >>> ${label}: sdist build deps (managed) >>>";

  # After a `uv lock`: update the block where the bench has one, and say once
  # how to get one where it does not. Never fails the caller over a bench that
  # keeps its own list.
  sync = pkgs.writeShellApplication {
    name = "frappe-nix-build-deps-sync";
    runtimeInputs = [
      command
      pkgs.gnugrep
    ];
    text = ''
      pyproject="''${1:-pyproject.toml}"
      lock="''${2:-uv.lock}"
      if grep -qF -- ${lib.escapeShellArg begin} "$pyproject"; then
        exec frappe-nix-build-deps update "$pyproject" "$lock"
      fi
      echo "frappe-nix: $pyproject keeps a hand-kept [tool.uv.extra-build-dependencies];" \
        "to derive it from $lock, see frappe-nix's docs/development/locks.md, \"Moving a bench to the derived list\"" >&2
    '';
  };
in
{
  inherit
    label
    always
    begin
    command
    sync
    ;

  # frappe-nix's own dev/ project: Frappe from a URL archive (flit-core, which
  # the lock does not record) and frappe-runtime installed editable (through
  # `editables`).
  dev = mk (
    always
    // {
      frappe = [ "flit-core" ];
      frappe-runtime = [
        "hatchling"
        "editables"
      ];
    }
  );
}
