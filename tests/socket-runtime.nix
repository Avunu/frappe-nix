# NixOS VM test for unix-socket mode with the UNIFIED runtime (the default).
#
# The split topology is covered by tests/socket.nix. What is under test here is
# what the unified runtime changes about the generated system:
#
#   * one unit per site (frappe-<site>) instead of web + socketio + scheduler +
#     one per queue,
#   * one nginx upstream, with /socket.io and / both pointing at it,
#   * no loopback :80 listener and no networking.hosts pin — the Python runtime
#     validates sessions in-process, so nothing has to reach the site by its own
#     FQDN over TCP,
#   * no socketio_uds in site_config.json, because nothing binds a second socket.
#
# Frappe is out of scope, as in socket.nix: a stub frappe-runtime stands in and
# echoes the headers it was given.
#
# Run: nix build .#checks.x86_64-linux.socket-runtime -L
{ self, pkgs }:
let
  siteName = "test.local";
  sockDir = "/run/frappe-test";

  # Stands in for the real frappe-runtime. Answers both / and /socket.io/ off the
  # same listener, which is the whole point of the topology under test.
  fakeRuntime = pkgs.writeScriptBin "frappe-runtime" ''
    #!${pkgs.python3}/bin/python3
    import json, os, socket, sys
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from socketserver import UnixStreamServer

    uds = sys.argv[sys.argv.index("--uds") + 1]

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({
                "path": self.path,
                **{k.lower(): v for k, v in self.headers.items()},
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    class S(UnixStreamServer, ThreadingHTTPServer):
        # BaseHTTPRequestHandler wants a (host, port) pair for logging.
        def get_request(self):
            conn, _ = super().get_request()
            return conn, ("local", 0)

    if os.path.exists(uds):
        os.unlink(uds)
    srv = S(uds, H)
    # Real uvicorn leaves the socket at the process umask; the directory is the
    # access gate. Reproduce that so a regression relying on socket permissions
    # would be caught here.
    os.chmod(uds, 0o777)
    srv.serve_forever()
  '';

  stubBench =
    pkgs.runCommand "stub-bench"
      {
        passthru = {
          pythonEnv = pkgs.buildEnv {
            name = "stub-python-env";
            paths = [ fakeRuntime ];
          };
          nodejs = pkgs.nodejs_22;
          appsPath = _: "/stub/apps";
        };
      }
      ''
        mkdir -p $out/bench/apps $out/bench/env $out/bench/config $out/bench/sites
        echo '{}' > $out/bench/sites/common_site_config.json
        # The registry a real package carries (lib/bench.nix).
        printf 'frappe\n' > $out/bench/sites/apps.txt
        printf '{"frappe": {"idx": 1}}\n' > $out/bench/sites/apps.json
        : > $out/bench/config/.keep
      '';
in
{
  name = "frappe-socket-runtime";

  nodes.machine =
    { ... }:
    {
      imports = [ self.nixosModules.default ];
      virtualisation.memorySize = 2048;
      environment.systemPackages = [ pkgs.jq ];

      services.frappe = {
        enable = true;
        package = stubBench;
        # The default, stated for the reader.
        runtime.enable = true;
        sites."${siteName}" = {
          enable = true;
          database.createLocally = true;
          nginx.enable = true;
          nginx.socketPath = "${sockDir}/nginx.sock";
          web.socketPath = "${sockDir}/web.sock";
        };
      };
    };

  testScript = ''
    import json

    start_all()
    machine.wait_for_unit("nginx.service")
    machine.wait_for_unit("frappe-${siteName}.service")
    machine.wait_for_file("${sockDir}/web.sock")
    machine.wait_for_file("${sockDir}/nginx.sock")

    # One unit, not five. The split units must not exist at all.
    for unit in ("frappe-web", "frappe-socketio", "frappe-scheduler",
                 "frappe-worker-default", "frappe-worker-short", "frappe-worker-long"):
        machine.fail(f"systemctl cat {unit}-${siteName}.service")

    # Same access model as the split topology: the directory is the gate.
    machine.succeed("stat -c '%a %U:%G' ${sockDir} | grep -x '770 frappe:frappe'")
    machine.succeed("id -nG nginx | tr ' ' '\\n' | grep -qx frappe")

    # Both locations reach the one process, over both unix hops.
    root = json.loads(
        machine.succeed(
            "curl -sS --unix-socket ${sockDir}/nginx.sock"
            " -H 'CF-Connecting-IP: 203.0.113.7' http://${siteName}/"
        )
    )
    assert root["x-forwarded-proto"] == "https", root
    assert root["x-real-ip"] == "203.0.113.7", root
    assert root["x-frappe-site-name"] == "${siteName}", root

    realtime = json.loads(
        machine.succeed(
            "curl -sS --unix-socket ${sockDir}/nginx.sock"
            " http://${siteName}/socket.io/?EIO=4"
        )
    )
    assert realtime["path"].startswith("/socket.io/"), realtime
    # nginx rewrites Origin so the runtime's host==origin check passes.
    assert realtime["origin"] == "https://${siteName}", realtime

    # No second realtime socket, so nothing should advertise one.
    machine.succeed(
        "jq -e '.socketio_uds == null'"
        " /var/lib/frappe/${siteName}/sites/${siteName}/site_config.json"
    )
    machine.fail("test -e ${sockDir}/socketio.sock")

    # The loopback :80 listener and the FQDN pin existed only for node's
    # session-validation callback. Both should be gone.
    machine.fail("ss -HltnO | grep -qE ':80\\s'")
    machine.fail("grep -qE '^127\\.0\\.0\\.1\\s+${siteName}' /etc/hosts")

    # And still nothing on TCP anywhere in the public path.
    machine.fail("ss -HltnO | grep -qE ':8000\\s'")
    machine.fail("ss -HltnO | grep -qE ':9000\\s'")

    # The app registry is linked from the package, not copied; the operator's
    # common_site_config.json is a real file.
    sites = "/var/lib/frappe/${siteName}/sites"
    for f in ("apps.txt", "apps.json"):
        machine.succeed(f"readlink {sites}/{f} | grep -qx '${stubBench}/bench/sites/{f}'")
    machine.succeed(f"test -f {sites}/common_site_config.json -a ! -L {sites}/common_site_config.json")

    # A regular file left by an older frappe-nix's copy-once seed must be
    # replaced on the next activation, not kept.
    machine.succeed(f"rm {sites}/apps.txt && echo stale > {sites}/apps.txt")
    machine.succeed("systemctl restart frappe-init-${siteName}.service")
    machine.wait_for_unit("frappe-${siteName}.service")
    machine.succeed(f"test -L {sites}/apps.txt && grep -qx frappe {sites}/apps.txt")
  '';
}
