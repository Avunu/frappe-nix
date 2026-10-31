# lib/port-lease.py, the dev shell's runtime port leases (`frappe-ports`), and
# lib/port-lease-ready.sh, the readiness check its probes run, on
# the build sandbox's own loopback: see port-lease.sh. The sandbox has a
# network namespace of its own, so its abstract sockets and ports are nobody
# else's.
{ pkgs, checks }:

{
  port-lease =
    checks.script "port-lease"
      {
        tools = [
          pkgs.jq
          pkgs.coreutils
        ];
      }
      ''
        export HOME="$PWD"
        bash ${./port-lease.sh} ${pkgs.python3Minimal}/bin/python3 ${../lib/port-lease.py} 41300 \
          ${pkgs.lib.getExe (import ../lib/port-lease-ready.nix { inherit pkgs; })} 2>&1 | tee "$out"
      '';
}
