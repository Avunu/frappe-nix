# The asset-shadow reassert check (lib/assets-reassert.nix), rendered and
# driven against a synthetic sites/assets/ tree and a stub bench/redis-cli —
# the asset half of issue #32's reconcile.
#
# Rendered and tested standalone (not through modules/devenv.nix) — the
# whole point of factoring the check into its own file.
{ pkgs, checks }:

let
  inherit (pkgs) lib;

  render =
    hooks:
    import ../lib/assets-reassert.nix {
      inherit lib pkgs;
      benchBin = "bench";
      redisCli = "redis-cli";
      site = "test.local";
      inherit hooks;
    };

  oneHook = render [ "myapp.build.reassert_assets" ];
  twoHooks = render [
    "myapp.build.reassert_assets"
    "myapp.build.another_hook"
  ];
in
{
  assets-reassert =
    checks.script "assets-reassert"
      {
        tools = with pkgs; [
          jq
          coreutils
          shellcheck
        ];
        drvArgs = {
          one = oneHook;
          two = twoHooks;
          passAsFile = [
            "one"
            "two"
          ];
        };
      }
      ''
        export HOME="$PWD"

        # devenv would ship these unlinted; lint them here instead.
        shellcheck -s bash -S warning -e SC2317 "$onePath"
        shellcheck -s bash -S warning -e SC2317 "$twoPath"

        SECOND_HOOK_SCRIPT="$twoPath" bash ${./assets-reassert.sh} "$onePath" | tee "$out"
      '';
}
