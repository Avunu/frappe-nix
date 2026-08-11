"""Object-store guard — keep File writes and deletes on local disk.

The `cloud_storage` app replaces Frappe's local-disk File storage with an
S3-protocol object store. On a bench restored from production that store is
*the production bucket*, and its keys carry no site prefix, so:

- deleting any File doc issues a real ``delete_object`` — and Frappe's own
  ``delete_old_exported_report_files`` runs hourly, so a dev bench starts
  permanently destroying production files within an hour of coming up;
- writing a File ``put_object``s over whatever production had at the same key.

Rather than blocking, this forces the app's own local mode. `cloud_storage`
already branches on ``frappe.conf.cloud_storage_settings["use_local"]`` and
falls back to ``save_file_on_filesystem()`` / ``delete_file_from_filesystem()``
— a path its author wrote for exactly this situation. Flipping that one flag
covers every branch (write, delete, retrieve, share, get_content, pdf preview,
the migration commands) with a single patch, and nothing has to be blocked.

Consequence worth knowing: File rows inherited from the production dump carry
``?key=`` URLs whose objects are not on local disk, so their previews 404 in
dev. That is strictly safer than the alternative.
"""

from .._hook import on_import
from .._patch import announce, mark, require
from .._settings import settings

NAME = "objectstore"

#: site_config blocks whose S3 client we redirect to local disk, and the key
#: each app uses to mean "don't talk to the object store".
_LOCAL_MODE_KEYS = {"cloud_storage_settings": "use_local"}

_INSTALLED = False


def install():
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    on_import("frappe", _patch_get_site_config)


def _patch_get_site_config(module):
    original = require(module, "get_site_config")

    def get_site_config(*args, **kwargs):
        config = original(*args, **kwargs)
        if not settings().guard_enabled(NAME):
            return config
        for block, flag in _LOCAL_MODE_KEYS.items():
            section = config.get(block)
            if isinstance(section, dict) and not section.get(flag):
                section[flag] = True
                announce(
                    NAME,
                    f"{block} is forced to local disk — this bench will not write to, "
                    "or delete from, the configured object store",
                )
        return config

    # frappe.init does `local.conf = _dict(get_site_config())`, resolving this
    # name from the frappe module at call time, so the wrapper is picked up.
    module.get_site_config = mark(get_site_config, "frappe.get_site_config")
