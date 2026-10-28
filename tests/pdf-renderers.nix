# NixOS VM test for services.frappe-nix.chromium and services.frappe-nix.wkhtmltopdf: a
# PDF rendered the way each of Frappe's generators renders one, from inside a
# site's own unit.
#
# Frappe itself is out of scope, as in socket-runtime.nix. A stub frappe-runtime
# stands in and renders one PDF with each:
#
#   * Chromium, as frappe.utils.pdf_generator does: found at
#     <bench>/chromium/chrome-linux/headless_shell, which is where
#     find_or_download_chromium_executable() looks when common_site_config.json
#     names no chromium_path, started with ChromePDFGenerator's own flags, the
#     DevTools URL read off its stderr, and the page rendered through a fresh
#     browser context, setDocumentContent and Page.printToPDF.
#   * wkhtmltopdf, as frappe.utils.pdf.get_pdf does: `wkhtmltopdf --version` by
#     bare name, then pdfkit.from_string with get_pdf's options, which finds the
#     binary by running `which`.
#
# Running in the unit, rather than as a command from the test script, is the
# point: it gets the unit's user, HOME, PATH and LD_LIBRARY_PATH, which are what
# would break either in production. The test then checks that each PDF's text
# survived, which needs fonts.
#
# Also under test: the Chromium link replaces what an earlier download left
# behind, a rerun of frappe-init keeps it, and the `bench` wrapper makes it and
# puts wkhtmltopdf on its PATH.
#
# Run: nix build .#checks.x86_64-linux.pdf-renderers -L
{ self, pkgs }:
let
  inherit (pkgs) lib;

  siteName = "test.local";
  siteDir = "/var/lib/frappe/${siteName}";
  linkDir = "${siteDir}/bench/chromium/chrome-linux";
  probeText = "frappe-nix pdf probe";

  probePython = pkgs.python3.withPackages (ps: [
    ps.pdfkit
    ps.websockets
  ]);

  # Stands in for frappe-runtime: render one PDF with each engine, then idle
  # like a server would. Writes <siteDir>/probe-<engine>.pdf, or
  # <siteDir>/probe-<engine>.err with what went wrong.
  probe = pkgs.writeScriptBin "frappe-runtime" ''
    #!${probePython}/bin/python3
    import asyncio, base64, json, os, signal, subprocess, sys, threading, time, traceback

    import pdfkit
    import websockets

    SITE_DIR = "${siteDir}"
    HTML = '<p style="font-family: Arial; font-size: 24pt">${probeText}</p>'

    # ChromePDFGenerator.start_chromium_process, the non-debug branch.
    FLAGS = [
        "--remote-debugging-port=0",
        "--disable-gpu",
        "--disable-field-trial-config",
        "--disable-background-networking",
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-back-forward-cache",
        "--disable-breakpad",
        "--disable-client-side-phishing-detection",
        "--disable-component-extensions-with-background-pages",
        "--disable-component-update",
        "--no-default-browser-check",
        "--disable-default-apps",
        "--disable-dev-shm-usage",
        "--disable-extensions",
        "--disable-features=ImprovedCookieControls,LazyFrameLoading,GlobalMediaControls,DestroyProfileOnBrowserClose,MediaRouter,DialMediaRouteProvider,AcceptCHFrame,AutoExpandDetailsElement,CertificateTransparencyComponentUpdater,AvoidUnnecessaryBeforeUnloadCheckSync,Translate,HttpsUpgrades,PaintHolding,ThirdPartyStoragePartitioning,LensOverlay,PlzDedicatedWorker",
        "--allow-pre-commit-input",
        "--disable-hang-monitor",
        "--disable-ipc-flooding-protection",
        "--disable-popup-blocking",
        "--disable-prompt-on-repost",
        "--disable-renderer-backgrounding",
        "--force-color-profile=srgb",
        "--metrics-recording-only",
        "--no-first-run",
        "--password-store=basic",
        "--use-mock-keychain",
        "--no-service-autorun",
        "--export-tagged-pdf",
        "--disable-search-engine-choice-screen",
        "--unsafely-disable-devtools-self-xss-warnings",
        "--enable-use-zoom-for-dsf=false",
        "--use-angle",
        "--headless",
        "--hide-scrollbars",
        "--mute-audio",
        "--blink-settings=primaryHoverType=2,availableHoverTypes=2,primaryPointerType=4,availablePointerTypes=4",
        "--no-sandbox",
        "--no-startup-window",
    ]


    def find_chromium():
        # find_or_download_chromium_executable(), past the chromium_path check.
        path = os.path.join(os.environ["FRAPPE_BENCH_ROOT"], "chromium", "chrome-linux", "headless_shell")
        if not os.path.exists(path):
            raise RuntimeError(f"no Chromium at {path}: Frappe would download one")
        if not os.access(path, os.X_OK):
            raise RuntimeError(f"Chromium not executable at {path}")
        return path


    def start(path):
        proc = subprocess.Popen([path, *FLAGS], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        started = time.time()
        seen = []
        while time.time() - started < 60:
            line = proc.stderr.readline()
            if not line:
                raise RuntimeError(f"Chromium exited ({proc.wait()}):\n" + "".join(seen))
            seen.append(line)
            if "DevTools listening on" in line:
                # Frappe gives it chromium_start_timeout, 3s by default.
                print(f"Chromium up in {time.time() - started:.1f}s", file=sys.stderr, flush=True)
                # Keep draining its stderr into the journal, as context for a failure.
                threading.Thread(target=lambda: [sys.stderr.write(l) for l in proc.stderr], daemon=True).start()
                return proc, line[line.find("ws://"):].strip()
        raise RuntimeError("no DevTools URL from Chromium in 60s:\n" + "".join(seen))


    async def render(url):
        async with websockets.connect(url, max_size=None) as ws:
            ids = iter(range(1, 1 << 30))

            async def call(method, params=None, session=None):
                msg = {"id": next(ids), "method": method, "params": params or {}}
                if session:
                    msg["sessionId"] = session
                await ws.send(json.dumps(msg))
                while True:
                    reply = json.loads(await ws.recv())
                    if reply.get("id") == msg["id"]:
                        if "error" in reply:
                            raise RuntimeError(f"{method}: {reply['error']}")
                        return reply["result"]

            context = (await call("Target.createBrowserContext"))["browserContextId"]
            target = (await call("Target.createTarget", {"url": "about:blank", "browserContextId": context}))["targetId"]
            session = (await call("Target.attachToTarget", {"targetId": target, "flatten": True}))["sessionId"]
            await call("Page.enable", session=session)
            frame = (await call("Page.getFrameTree", session=session))["frameTree"]["frame"]["id"]
            await call("Page.setDocumentContent", {"frameId": frame, "html": HTML}, session=session)
            pdf = await call("Page.printToPDF", {"printBackground": True}, session=session)
            return base64.b64decode(pdf["data"])


    def chromium():
        proc, url = start(find_chromium())
        try:
            return asyncio.run(render(url))
        finally:
            proc.terminate()


    def wkhtmltopdf():
        # get_wkhtmltopdf_version(), which get_pdf() calls first.
        version = subprocess.check_output(["wkhtmltopdf", "--version"], text=True).strip()
        print(f"wkhtmltopdf: {version}", file=sys.stderr, flush=True)
        # prepare_options() and get_pdf(), less the page size and cookies.
        options = {
            "print-media-type": None,
            "background": None,
            "images": None,
            "quiet": None,
            "encoding": "UTF-8",
            "margin-right": "15mm",
            "margin-left": "15mm",
            "disable-javascript": "",
            "disable-local-file-access": "",
            "disable-smart-shrinking": "",
        }
        return pdfkit.from_string(HTML, options=options, verbose=True)


    # Each engine on its own, so one failing does not hide the other.
    for engine, render_pdf in (("chromium", chromium), ("wkhtmltopdf", wkhtmltopdf)):
        out = os.path.join(SITE_DIR, f"probe-{engine}")
        try:
            pdf = render_pdf()
            with open(out + ".pdf.tmp", "wb") as f:
                f.write(pdf)
            os.rename(out + ".pdf.tmp", out + ".pdf")
        except Exception:
            traceback.print_exc()
            with open(out + ".err", "w") as f:
                f.write(traceback.format_exc())
        finally:
            sys.stderr.flush()

    signal.pause()
  '';

  # The `bench` wrapper execs this. It only has to find wkhtmltopdf as pdfkit
  # would, on the PATH the wrapper gives it.
  stubBenchCli = pkgs.writeShellScriptBin "bench" "which wkhtmltopdf";

  stubBench =
    pkgs.runCommand "stub-bench"
      {
        passthru = {
          pythonEnv = pkgs.buildEnv {
            name = "stub-python-env";
            paths = [
              probe
              stubBenchCli
            ];
          };
          nodejs = pkgs.nodejs_22;
          appsPath = _: "/stub/apps";
        };
      }
      ''
        mkdir -p $out/bench/apps $out/bench/env $out/bench/config $out/bench/sites
        echo '{}' > $out/bench/sites/common_site_config.json
        printf 'frappe\n' > $out/bench/sites/apps.txt
        printf '{"frappe": {"idx": 1}}\n' > $out/bench/sites/apps.json
        : > $out/bench/config/.keep
      '';
in
{
  nodes.machine =
    { ... }:
    {
      imports = [ self.nixosModules.default ];
      virtualisation.memorySize = 2048;
      environment.systemPackages = [ pkgs.poppler-utils ];

      services.frappe-nix = {
        # Under the module's sandbox: hardening.enable defaults to true.
        enable = true;
        package = stubBench;
        chromium.enable = true;
        wkhtmltopdf.enable = true;
        # Nothing to migrate against: no database in this VM.
        migrate.enable = false;
        sites."${siteName}".enable = true;
      };
    };

  testScript =
    { nodes, ... }:
    let
      chromiumExe = lib.getExe nodes.machine.services.frappe-nix.chromium.package;
      wkhtmltopdfExe = "${nodes.machine.services.frappe-nix.wkhtmltopdf.package}/bin/wkhtmltopdf";
    in
    ''
      start_all()
      machine.wait_for_unit("frappe-init-${siteName}.service")

      # The link, to the module's Chromium: chrome-headless-shell by default.
      machine.succeed("test -L ${linkDir}/headless_shell")
      machine.succeed('test "$(readlink ${linkDir}/headless_shell)" = ${chromiumExe}')

      # A PDF from each engine, from inside the site's unit.
      machine.wait_for_unit("frappe-${siteName}.service")
      for engine in ("chromium", "wkhtmltopdf"):
          base = f"${siteDir}/probe-{engine}"
          machine.wait_until_succeeds(f"test -e {base}.pdf -o -e {base}.err", timeout=180)
          if machine.succeed(f"test -e {base}.err && echo err || true").strip() == "err":
              print(machine.succeed(f"cat {base}.err"))
              print(machine.succeed("journalctl -u frappe-${siteName}.service -o cat"))
              raise Exception(f"the probe could not render a PDF with {engine}")

          text = machine.succeed(f"pdftotext {base}.pdf -")
          assert "${probeText}" in text, f"{engine}: the PDF's text did not survive: {text!r}"
          # Arial, through fontconfig's alias for it.
          fonts = machine.succeed(f"pdffonts {base}.pdf")
          assert "LiberationSans" in fonts, f"{engine}: {fonts}"

      journal = machine.succeed("journalctl -u frappe-${siteName}.service -o cat")
      assert "Chromium up in" in journal, journal
      # The build Frappe's header and footer options need.
      assert "with patched qt" in journal, journal

      # What an earlier `bench setup-chrome` or Print Designer download left
      # behind goes, and the link takes its place.
      machine.succeed(
          "rm ${linkDir}/headless_shell"
          " && printf 'ELF' > ${linkDir}/headless_shell && chmod +x ${linkDir}/headless_shell"
          " && touch ${linkDir}/libEGL.so"
      )
      machine.succeed("systemctl restart frappe-init-${siteName}.service")
      machine.succeed('test "$(readlink ${linkDir}/headless_shell)" = ${chromiumExe}')
      machine.fail("test -e ${linkDir}/libEGL.so")

      # A rerun over the link keeps it.
      machine.succeed("systemctl restart frappe-init-${siteName}.service")
      machine.succeed('test "$(readlink ${linkDir}/headless_shell)" = ${chromiumExe}')

      # The `bench` wrapper makes the link as well, and puts wkhtmltopdf on its
      # own PATH, for a PDF rendered by hand: nothing else on this machine has it.
      machine.succeed("rm -r ${siteDir}/bench/chromium")
      found = machine.succeed("sudo -u frappe env FRAPPE_SITE=${siteName} bench --version").strip()
      assert found == "${wkhtmltopdfExe}", found
      machine.succeed('test "$(readlink ${linkDir}/headless_shell)" = ${chromiumExe}')
    '';
}
