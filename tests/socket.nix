# NixOS VM test for unix-socket mode (sites.<name>.nginx.socketPath +
# sites.<name>.web.socketPath + sites.<name>.socketio.socketPath).
#
# Frappe itself is out of scope — like migrate-rollback.nix this stubs the bench.
# What is under test is the request path this module generates:
#
#   curl --unix-socket <nginx.socketPath>   ->  nginx
#     -> upstream frappe-web-<flattened>    ->  unix:<web.socketPath>  -> gunicorn
#
# i.e. two unix hops and no TCP anywhere in the public path. The stub gunicorn
# echoes the headers it received, so the assertions cover the two things that
# silently break over a unix socket: the client IP (no peer address exists) and
# the public scheme (nginx's $scheme is "http" behind an edge that terminated TLS).
#
# Run: nix build .#checks.x86_64-linux.socket -L
{ self, pkgs }:
let
  siteName = "test.local";

  # Real gunicorn defaults to `--umask 0`, so its unix socket lands 0777 — the
  # directory around it is the actual access gate. Reproduce that here so the
  # test would catch a regression that relied on socket permissions instead.
  fakeGunicorn = pkgs.writeScriptBin "gunicorn" ''
    #!${pkgs.python3}/bin/python3
    import json, os, socket, socketserver, sys
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    bind = sys.argv[sys.argv.index("--bind") + 1]

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        # AF_UNIX accept() yields an empty peer address, which the default
        # implementation cannot format.
        def address_string(self):
            return "unix"

        def do_GET(self):
            body = json.dumps({k.lower(): v for k, v in self.headers.items()}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    if bind.startswith("unix:"):
        path = bind[len("unix:"):]
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass

        class UnixHTTPServer(ThreadingHTTPServer):
            address_family = socket.AF_UNIX

            def server_bind(self):
                # Skip HTTPServer.server_bind: it calls getfqdn on the address.
                socketserver.TCPServer.server_bind(self)
                self.server_name = "localhost"
                self.server_port = 0

        os.umask(0)
        srv = UnixHTTPServer(path, H)
    else:
        host, _, port = bind.rpartition(":")
        srv = ThreadingHTTPServer((host, int(port)), H)

    srv.serve_forever()
  '';

  fakeBench = pkgs.writeShellScriptBin "bench" ''
    case "''${1:-}" in
      schedule|worker) exec sleep infinity ;;
      *) : ;;
    esac
  '';

  stubPyEnv = pkgs.buildEnv {
    name = "stub-frappe-pyenv";
    paths = [
      fakeBench
      fakeGunicorn
    ];
  };
  # The realtime server reads socketio_uds and does `server.listen(uds || port)`.
  # Stub that: bind the socket so the test can prove nothing reached TCP 9000.
  stubNode = pkgs.writeShellScriptBin "node" ''
    if [ -n "''${FRAPPE_SOCKETIO_UDS:-}" ]; then
      exec ${pkgs.python3}/bin/python3 -c '
    import os, socket, sys
    path = os.environ["FRAPPE_SOCKETIO_UDS"]
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(path)
    s.listen(8)
    while True:
        s.accept()[0].close()
    '
    fi
    exec sleep infinity
  '';

  stubBench =
    pkgs.runCommand "stub-bench"
      {
        passthru = {
          pythonEnv = stubPyEnv;
          nodejs = stubNode;
          appsPath = benchDir: "${benchDir}/apps";
          appNames = [ "frappe" ];
          extraPackages = [ ];
        };
      }
      ''
        mkdir -p $out/bench/apps $out/bench/env $out/bench/config $out/bench/sites
        echo '{}' > $out/bench/sites/common_site_config.json
        : > $out/bench/config/.keep
      '';

  sockDir = "/run/frappe-test";
in
{
  name = "frappe-socket";

  nodes.machine =
    { ... }:
    {
      imports = [ self.nixosModules.default ];
      virtualisation.memorySize = 2048;
      environment.systemPackages = [ pkgs.jq ];

      services.frappe = {
        enable = true;
        package = stubBench;
        sites."${siteName}" = {
          enable = true;
          database.createLocally = true;
          nginx.enable = true;
          nginx.socketPath = "${sockDir}/nginx.sock";
          web.socketPath = "${sockDir}/web.sock";
          socketio.socketPath = "${sockDir}/socketio.sock";
        };
      };
    };

  testScript = ''
    import json

    start_all()
    machine.wait_for_unit("nginx.service")
    machine.wait_for_unit("frappe-web-${siteName}.service")
    machine.wait_for_unit("frappe-socketio-${siteName}.service")
    machine.wait_for_file("${sockDir}/web.sock")
    machine.wait_for_file("${sockDir}/nginx.sock")
    machine.wait_for_file("${sockDir}/socketio.sock")

    # The directory is the access gate — both sockets are permissive by design
    # (nginx chmods its own to 0666; gunicorn's follows its umask). 0770 rather
    # than 0750 because nginx *creates* its socket here as a mere group member.
    machine.succeed("stat -c '%a %U:%G' ${sockDir} | grep -x '770 frappe:frappe'")
    # nginx can only traverse it via group membership
    machine.succeed("id -nG nginx | tr ' ' '\\n' | grep -qx frappe")

    # End to end over both unix hops.
    hdrs = json.loads(
        machine.succeed(
            "curl -sS --unix-socket ${sockDir}/nginx.sock"
            " -H 'CF-Connecting-IP: 203.0.113.7' http://${siteName}/"
        )
    )
    # TLS was terminated at the edge; $scheme over a unix socket would say http.
    assert hdrs["x-forwarded-proto"] == "https", hdrs
    # A unix socket has no peer address, so this only works via real_ip.
    assert hdrs["x-real-ip"] == "203.0.113.7", hdrs
    assert hdrs["x-forwarded-for"] == "203.0.113.7", hdrs
    # The site name header the module injects still arrives.
    assert hdrs["x-frappe-site-name"] == "${siteName}", hdrs

    # socketio_uds reached the config as well as the unit environment, so the
    # realtime server finds the socket even started outside its unit.
    machine.succeed(
        "jq -e '.socketio_uds == \"${sockDir}/socketio.sock\"'"
        " /var/lib/frappe/${siteName}/sites/${siteName}/site_config.json"
    )

    # The loopback listener is intentional (socketio's session-validation
    # callback resolves the site FQDN to 127.0.0.1, and node's fetch cannot
    # speak unix) but must not be public.
    machine.succeed("ss -HltnO | grep -qE '127\\.0\\.0\\.1:80\\s'")
    machine.fail("ss -HltnO | grep -qE '(0\\.0\\.0\\.0|\\*):80\\s'")

    # Neither gunicorn nor the realtime server is on TCP in this mode: the
    # loopback :80 above is the only listener the site has.
    machine.fail("ss -HltnO | grep -qE ':8000\\s'")
    machine.fail("ss -HltnO | grep -qE ':9000\\s'")
  '';
}
