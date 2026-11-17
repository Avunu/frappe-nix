# services.frappe-nix.chromium and services.frappe-nix.wkhtmltopdf, checked at
# evaluation: what the units and the `bench` wrapper get with each on, and with
# both off, and the fonts that come with them. And the dev shell's link to the
# same browser (lib/chrome-headless-shell.nix), which keeps Print Designer's
# install from downloading one: the link snippet is run against a bench
# directory, and the shell's enterShell is shown to carry it.
#
# Pure evaluation of three NixOS configurations against a stub bench, like
# tests/offline-migrate-unit.nix. Stubs stand in for both packages so this check
# does not pull either closure; tests/pdf-renderers.nix renders a PDF with the
# real ones in a VM.
{
  self,
  pkgs,
  checks,
}:

let
  inherit (pkgs) lib;

  site = "erp.example.com";

  stubBench =
    pkgs.runCommand "stub-bench"
      {
        passthru = {
          inherit (pkgs) nodejs;
          pythonEnv = pkgs.emptyDirectory;
          appsPath = _: "/stub/apps";
        };
      }
      ''
        mkdir -p $out/bench
      '';

  stubChromium = pkgs.writeShellScriptBin "chromium" "exit 0";
  stubWkhtmltopdf = pkgs.writeShellScriptBin "wkhtmltopdf" "exit 0";

  eval =
    renderers:
    (import (pkgs.path + "/nixos/lib/eval-config.nix") {
      inherit (pkgs.stdenv.hostPlatform) system;
      modules = [
        self.nixosModules.default
        {
          nixpkgs.pkgs = pkgs;
          system.stateVersion = lib.trivial.release;
          services.frappe-nix = {
            enable = true;
            package = stubBench;
            sites.${site}.enable = true;
          }
          // renderers;
        }
      ];
    }).config;

  initOf = config: config.systemd.services."frappe-init-${site}".serviceConfig.ExecStart;
  benchOf =
    config:
    # The inner script: the `bench` on PATH only picks who runs it.
    "${(lib.findFirst (p: (p.name or "") == "bench") null config.environment.systemPackages).inner}";
  # Whether the site's runtime unit has a package's bin/ on its PATH, or any
  # wkhtmltopdf at all. Decided here, and the default package below is named
  # without its context, so the check builds none of them.
  onPathOf =
    config: needle:
    let
      plain = builtins.unsafeDiscardStringContext;
    in
    lib.boolToString (
      lib.hasInfix (plain needle) (plain config.systemd.services."frappe-${site}".environment.PATH)
    );
  # Whether every font the options promise is in fonts.packages.
  fontsOf =
    config:
    let
      names = map lib.getName config.fonts.packages;
    in
    lib.boolToString (
      lib.all (f: lib.elem (lib.getName f) names) [
        pkgs.liberation_ttf
        pkgs.noto-fonts
      ]
    );

  chromium = eval {
    chromium = {
      enable = true;
      package = stubChromium;
    };
  };
  wkhtmltopdf = eval {
    wkhtmltopdf = {
      enable = true;
      package = stubWkhtmltopdf;
    };
  };
  off = eval { };

  link = "chromium/chrome-linux/headless_shell";

  chrome = import ../lib/chrome-headless-shell.nix { inherit pkgs; };
  plain = builtins.unsafeDiscardStringContext;

  # The dev shell of a bench flake, as tests/ide.nix builds one. Its enterShell is
  # read as a value, without its context: the real browser is not built here.
  devShell =
    (import ./standards/fixtures/bench-flake.nix { inherit self pkgs; } (_: {
      frappe-nix = {
        enable = true;
        name = "pdf-bench";
        workspaceRoot = ./fixtures/ide-bench;
        python = pkgs.python314;
      };
    })).devenv.shells.default;
  devEnterShell = pkgs.writeText "dev-enterShell" (plain devShell.enterShell);
in
{
  pdf-renderers-unit = checks.script "pdf-renderers-unit" { } ''
    fails=0
    ok() { printf '  \033[32m✓\033[0m %s\n' "$1"; }
    no() { printf '  \033[31m✗\033[0m %s\n' "$1"; fails=$((fails + 1)); }
    has() { if grep -qF -- "$2" "$3"; then ok "$1"; else no "$1 (missing: $2)"; fi; }
    lacks() { if grep -qF -- "$2" "$3"; then no "$1 (found: $2)"; else ok "$1"; fi; }
    is() { if [ "$2" = "$3" ]; then ok "$1"; else no "$1 (got '$2', want '$3')"; fi; }

    echo "chromium.enable = true:"
    has "frappe-init links the package's main program where Frappe looks" \
      'ln -sfn ${lib.getExe stubChromium} "/var/lib/frappe/${site}/bench/${link}"' ${initOf chromium}
    has "…clearing what an earlier download left, only when it is not a link" \
      'if [ ! -L "/var/lib/frappe/${site}/bench/${link}" ]; then' ${initOf chromium}
    has "the bench wrapper links it too" \
      'ln -sfn ${lib.getExe stubChromium} "$FRAPPE_BENCH_ROOT/${link}"' ${benchOf chromium}
    lacks "common_site_config.json is left to the operator" "chromium_path" ${initOf chromium}
    is "Liberation and Noto are in fonts.packages" "${fontsOf chromium}" true
    is "no wkhtmltopdf on the units' PATH" "${onPathOf chromium "wkhtmltopdf"}" false

    echo "wkhtmltopdf.enable = true:"
    is "the site's unit has it on PATH" "${onPathOf wkhtmltopdf "${stubWkhtmltopdf}/bin"}" true
    is "…with the which that pdfkit finds it through" "${onPathOf wkhtmltopdf "${pkgs.which}/bin"}" true
    has "the bench wrapper puts it on PATH too" "${stubWkhtmltopdf}/bin" ${benchOf wkhtmltopdf}
    is "Liberation and Noto are in fonts.packages" "${fontsOf wkhtmltopdf}" true
    lacks "frappe-init makes no Chromium link" "chromium" ${initOf wkhtmltopdf}

    echo "both off (the default):"
    lacks "frappe-init makes no link" "chromium" ${initOf off}
    lacks "the bench wrapper neither links nor adds anything" "chromium" ${benchOf off}
    lacks "…" "${builtins.unsafeDiscardStringContext "${pkgs.wkhtmltopdf}"}/bin" ${benchOf off}
    is "no wkhtmltopdf on the units' PATH" "${onPathOf off "wkhtmltopdf"}" false
    is "no fonts added" "${fontsOf off}" false

    echo "the dev shell (lib/chrome-headless-shell.nix):"
    has "enterShell links the headless shell into the bench, where Print Designer looks" \
      'ln -sfn ${plain (lib.getExe chrome.package)} "$FRAPPE_BENCH_ROOT/${link}"' ${devEnterShell}
    has "…clearing what an earlier download left, only when it is not a link" \
      'if [ ! -L "$FRAPPE_BENCH_ROOT/${link}" ]; then' ${devEnterShell}

    echo "the link, run against a bench directory:"
    bench=$PWD/bench
    mkdir -p "$bench"
    run_link() { ( export bench; bash -euc ${lib.escapeShellArg (chrome.link stubChromium "$bench")} ); }
    run_link
    is "a fresh bench gets the link" "$(readlink "$bench/${link}")" "${plain (lib.getExe stubChromium)}"
    # What Print Designer's own download leaves: a real directory with an ELF in it.
    rm -r "$bench/chromium"
    mkdir -p "$bench/chromium/chrome-linux"
    printf 'ELF' > "$bench/chromium/chrome-linux/headless_shell"
    printf 'zip' > "$bench/chromium/chrome-headless-shell-linux64.zip"
    run_link
    is "a download is replaced by the link" "$(readlink "$bench/${link}")" "${plain (lib.getExe stubChromium)}"
    if [ -e "$bench/chromium/chrome-headless-shell-linux64.zip" ]; then no "…with its leftovers gone"; else ok "…with its leftovers gone"; fi
    # A second run must not take the path away: remove nothing when it is a link.
    touch "$bench/chromium/marker"
    run_link
    if [ -e "$bench/chromium/marker" ]; then ok "a second run leaves the directory alone"; else no "a second run leaves the directory alone"; fi
    is "…and the link still points at the package" "$(readlink "$bench/${link}")" "${plain (lib.getExe stubChromium)}"
    if [ -x "$bench/${link}" ]; then ok "the link resolves to an executable"; else no "the link resolves to an executable"; fi

    if [ "$fails" -ne 0 ]; then
      echo "$fails check(s) failed" >&2
      exit 1
    fi
    echo "all pdf-renderers-unit checks passed" | tee "$out"
  '';
}
