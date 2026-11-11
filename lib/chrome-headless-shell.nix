# Chromium for Frappe's "chrome" PDF generator and Print Designer, shared by
# services.frappe-nix.chromium (modules/nixos.nix) and the dev shell
# (modules/devenv.nix).
#
# Both look for the browser at <bench>/chromium/chrome-linux/headless_shell, where
# `bench setup-chrome` downloads Google's chrome-headless-shell, unless
# common_site_config.json names a `chromium_path`. Left to themselves they
# download it, and that build does not run on NixOS: it expects an FHS dynamic
# loader. Print Designer does so while it installs, and fails the install when
# the download cannot be made (the dev shell's egress guard refuses it, so
# `frappe-test` could not install an app that requires print_designer), and
# retries from a scheduler event that fires every few minutes.
#
# So `link` a NixOS build of it where they look, before an app installs. This
# needs nothing in common_site_config.json, which is the operator's, and an
# operator's own `chromium_path` still wins, since both check it first. The link
# is re-made on every run, so it follows the package. The directory is removed
# only when the binary is not already a link, which is the leftover of an earlier
# download, so a routine run never leaves the path missing while a running
# process could be looking for it.
#
# The default package is the same chrome-headless-shell, patched for NixOS by
# nixpkgs' Playwright packaging, not pkgs.chromium. Frappe starts a browser for
# every PDF and stops it with SIGTERM. Full Chromium's headless mode makes a
# profile under ~/.config for each start, which fails outright under the NixOS
# module's root-owned home and, where it can write, leaves about 2 MB behind
# every time, since SIGTERM skips the cleanup. The headless shell writes no
# profile, and its closure is a quarter of the size.
{ pkgs }:
{
  # bin/ is added so lib.getExe finds it, as for any other chromium.package.
  # Chromium finds its resources through /proc/self/exe, which resolves the
  # link to the real directory.
  package =
    let
      shell = pkgs.playwright-driver.components.chromium-headless-shell;
    in
    pkgs.runCommand "chrome-headless-shell" { meta.mainProgram = "chrome-headless-shell"; } ''
      mkdir -p $out/bin
      # The directory is named for the platform: -linux64, -linux-arm64.
      ln -s ${shell}/chrome-headless-shell-*/chrome-headless-shell $out/bin/chrome-headless-shell
      test -x $out/bin/chrome-headless-shell
    '';

  # The shell that links `package`'s main program into the bench at BENCHROOT
  # (a path, or a shell expression such as "$FRAPPE_BENCH_ROOT").
  link = package: benchRoot: ''
    if [ ! -L "${benchRoot}/chromium/chrome-linux/headless_shell" ]; then
      rm -rf "${benchRoot}/chromium"
    fi
    mkdir -p "${benchRoot}/chromium/chrome-linux"
    ln -sfn ${pkgs.lib.getExe package} "${benchRoot}/chromium/chrome-linux/headless_shell"
  '';
}
