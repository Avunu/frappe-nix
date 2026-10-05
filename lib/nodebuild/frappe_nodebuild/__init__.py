"""Carry frappe-nix's esbuild preload into Frappe's own asset builds.

Loaded at interpreter start from ``zzz-frappe-nodebuild.pth`` inside the
virtualenv, like ``frappe_unixsock``, so it reaches every ``bench build`` and
``bench watch`` — the dev shell's, ``bench update``'s, ``bench get-app``'s and
builtBench's — without an app install or a ``site_config.json`` edit.

frappe-nix corrects frappe's esbuild pipeline from a module node loads with
``--require`` (``lib/js/esbuild-preload.js``: frappe's node_modules first in
nodePaths, object rest/spread lowered). Exporting ``NODE_OPTIONS`` before the
command cannot get it there: ``frappe.build`` starts node with
``env=get_node_env()``, and ``frappe.commands.popen`` merges that over
``os.environ``, so its ``NODE_OPTIONS`` — a heap size, nothing else — replaces
whatever the caller set. This appends ``--require=$FRAPPE_NIX_ESBUILD_PRELOAD``
to what ``get_node_env()`` returns, and leaves the rest as Frappe made it.

Inert unless ``FRAPPE_NIX_ESBUILD_PRELOAD`` is set: the dev shell exports it,
and so does builtBench's build phase. A deployed host never builds, so there it
is never set. Set and the target gone, importing ``frappe.build`` fails rather
than compiling assets the preload would have corrected; unset it for a single
command to build stock.
"""

import functools
import os

__all__ = ["ENV", "install"]

#: Path of the module the build's node loads with --require.
ENV = "FRAPPE_NIX_ESBUILD_PRELOAD"

_INSTALLED = False


def install():
    """Hook frappe.build when a preload is configured. Idempotent."""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    if not os.environ.get(ENV, "").strip():
        return

    from ._hook import on_import

    on_import("frappe.build", _patch_build)


def _patch_build(module):
    original = getattr(module, "get_node_env", None)
    if not callable(original):
        raise ImportError(
            f"frappe_nodebuild: frappe.build.get_node_env is gone, so {ENV} cannot "
            "reach frappe's esbuild. Unset it to build without frappe-nix's "
            "corrections, and update lib/nodebuild for this Frappe."
        )

    @functools.wraps(original)
    def get_node_env(*args, **kwargs):
        env = dict(original(*args, **kwargs))
        preload = os.environ.get(ENV, "").strip()
        if preload:
            flag = f"--require={preload}"
            options = env.get("NODE_OPTIONS", "")
            if flag not in options.split():
                env["NODE_OPTIONS"] = f"{options} {flag}".strip()
        return env

    module.get_node_env = get_node_env


# NB: install() is called by the .pth bootstrap, not here — see frappe_unixsock.
