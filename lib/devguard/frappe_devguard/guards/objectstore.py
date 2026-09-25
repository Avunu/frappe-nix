"""Object-store guard — additive-only access to the production bucket.

The `cloud_storage` app replaces Frappe's local-disk File storage with an
S3-protocol object store. On a bench restored from production that store is
*the production bucket*, and its keys carry no site prefix, so:

- deleting any File doc issues a real ``delete_object`` — and Frappe's own
  ``delete_old_exported_report_files`` runs hourly, so a dev bench starts
  permanently destroying production files within an hour of coming up;
- writing a File ``put_object``s over whatever production had at the same key.

Pushing new attachments *to* that bucket is, on the other hand, a routine and
deliberate way of moving data to production: documents travel as fixtures,
their attachments travel through the object store. So the guard has two modes
(``devguard.objectstore.mode``):

``local`` (default)
    Force the app's own local mode. `cloud_storage` already branches on
    ``frappe.conf.cloud_storage_settings["use_local"]`` and falls back to
    ``save_file_on_filesystem()`` / ``delete_file_from_filesystem()`` — a path
    its author wrote for exactly this situation. Flipping that one flag covers
    every branch (write, delete, retrieve, share, get_content, pdf preview, the
    migration commands) with a single patch. File rows inherited from the
    production dump carry ``?key=`` URLs whose objects are not on local disk,
    so their previews 404 in dev.

``push``
    Leave `cloud_storage` talking to the bucket, so new attachments land there
    and inherited ones resolve. What keeps this safe is the second layer below.

Under both modes, every botocore S3 client in the process is held to
*additive-only* access. This sits on ``BaseClient._make_api_call`` — the one
funnel every botocore operation passes through — so it holds for any app or
script, not only `cloud_storage`:

- reads (``Get*``, ``Head*``, ``List*``) pass through;
- ``DeleteObject`` / ``DeleteObjects`` are dropped without reaching the
  network, and reported as done (``DeleteObjects`` reports every key as an
  error instead, since it has a way to say so). A File deleted in dev loses its
  row, never production's object;
- in ``push`` mode, new objects may be written, but never over an existing
  key: the write is refused if ``HeadObject`` finds the key, and carries
  ``If-None-Match: *`` so the store itself refuses one that appears in the
  meantime (or that a write-only credential could not ``HeadObject``);
- in ``local`` mode, nothing is written at all;
- everything else — bucket policy, lifecycle rules (which can expire every
  object in a bucket), ACLs, tagging, retention, ``RenameObject`` — is refused
  in either mode;
- presigned URLs are only issued for reads, since a presigned write happens in
  someone's browser, outside the funnel above.

Clients pointed at a loopback endpoint (a local MinIO) are left alone, as the
integrations guard does for loopback hosts.

Consequences worth knowing in ``push`` mode: an upload whose key production
already holds (the same file name on the same document) is refused rather than
versioned, and a file pushed by mistake has to be removed from production, not
from dev.
"""

from urllib.parse import urlsplit

from .._hook import on_import
from .._patch import announce, block, mark, require, warn
from .._settings import settings

NAME = "objectstore"

#: site_config blocks whose S3 client we redirect to local disk, and the key
#: each app uses to mean "don't talk to the object store".
_LOCAL_MODE_KEYS = {"cloud_storage_settings": "use_local"}

MODES = ("local", "push")

_LOOPBACK = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}

#: Read-only operations with prefixes other than Get/Head/List.
_READS = {"SelectObjectContent"}

_DELETES = {"DeleteObject", "DeleteObjects"}

#: What ``push`` mode permits beyond reads: creating new objects, including
#: through multipart upload (which is how boto3's upload_file/upload_fileobj
#: send anything over its 8 MiB threshold). Aborting only discards an
#: unfinished upload's parts, never a stored object.
_WRITES = {
    "PutObject",
    "CopyObject",
    "CreateMultipartUpload",
    "UploadPart",
    "UploadPartCopy",
    "CompleteMultipartUpload",
    "AbortMultipartUpload",
}

#: Writes that name a destination key, checked with HeadObject before they run.
#: CreateMultipartUpload is included so a clash fails before any part uploads.
_CREATES_KEY = {"PutObject", "CopyObject", "CreateMultipartUpload"}

#: Writes that commit an object and accept If-None-Match.
_CONDITIONAL = {"PutObject", "CopyObject", "CompleteMultipartUpload"}

_ABSENT = {"404", "NoSuchKey", "NotFound"}

_PRESIGNABLE = {"get_object", "head_object"}

_INSTALLED = False


def install():
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    on_import("frappe", _patch_get_site_config)
    on_import("botocore.client", _patch_client)
    on_import("botocore.signers", _patch_signers)


def mode():
    value = settings().text(NAME, "mode", "local").strip().lower()
    if value not in MODES:
        warn(NAME, f"unknown mode {value!r}; falling back to 'local'")
        return "local"
    return value


def _patch_get_site_config(module):
    original = require(module, "get_site_config")

    def get_site_config(*args, **kwargs):
        config = original(*args, **kwargs)
        if not settings().guard_enabled(NAME) or mode() != "local":
            return config
        for block_name, flag in _LOCAL_MODE_KEYS.items():
            section = config.get(block_name)
            if isinstance(section, dict) and not section.get(flag):
                section[flag] = True
                announce(
                    NAME,
                    f"{block_name} is forced to local disk — this bench will not write to, "
                    "or delete from, the configured object store",
                )
        return config

    # frappe.init does `local.conf = _dict(get_site_config())`, resolving this
    # name from the frappe module at call time, so the wrapper is picked up.
    module.get_site_config = mark(get_site_config, "frappe.get_site_config")


# --------------------------------------------------------------------------
# botocore: additive-only S3
# --------------------------------------------------------------------------


def _is_loopback(client):
    endpoint = getattr(getattr(client, "meta", None), "endpoint_url", None) or ""
    return (urlsplit(endpoint).hostname or "").lower() in _LOOPBACK


def _guarded(client):
    """Is this an S3 client the guard applies to, right now?"""
    if not settings().guard_enabled(NAME):
        return False
    service = getattr(getattr(client, "meta", None), "service_model", None)
    if getattr(service, "service_name", None) != "s3":
        return False
    return not _is_loopback(client)


def _is_read(operation):
    return operation.startswith(("Get", "Head", "List")) or operation in _READS


def _where(params):
    bucket = params.get("Bucket", "?")
    key = params.get("Key")
    return f"s3://{bucket}/{key}" if key else f"s3://{bucket}"


def _error_code(exc):
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return None
    return str(response.get("Error", {}).get("Code", "")) or None


def _dropped_delete(operation, params):
    announce(
        NAME,
        "object-store deletes are dropped — this bench never removes objects "
        "from the configured bucket",
    )
    meta = {"HTTPStatusCode": 204 if operation == "DeleteObject" else 200}
    if operation == "DeleteObject":
        warn(NAME, f"kept {_where(params)}: DeleteObject dropped")
        return {"ResponseMetadata": meta}

    objects = params.get("Delete", {}).get("Objects", [])
    warn(NAME, f"kept {len(objects)} object(s) in {_where(params)}: DeleteObjects dropped")
    return {
        "ResponseMetadata": meta,
        "Errors": [
            {
                "Key": obj.get("Key"),
                "Code": "AccessDenied",
                "Message": "dropped by frappe_devguard",
            }
            for obj in objects
        ],
    }


def _refuse_overwrite(original, client, operation, params):
    """Raise if ``params`` names a key that already exists."""
    if operation not in _CREATES_KEY or "Key" not in params:
        return
    probe = {"Bucket": params.get("Bucket"), "Key": params["Key"]}
    try:
        original(client, "HeadObject", probe)
    except Exception as exc:  # noqa: BLE001 - classified below, never swallowed blindly
        if _error_code(exc) in _ABSENT:
            return
        # Most often 403 for a write-only credential, which cannot tell
        # "missing" from "forbidden". If-None-Match still stands behind this.
        warn(
            NAME,
            f"could not check {_where(params)} before writing "
            f"({type(exc).__name__}: {exc}); relying on If-None-Match",
        )
        return
    block(
        NAME,
        f"{operation} {_where(params)}: the key already exists in the object store, "
        "and this bench only adds objects, never replaces them",
    )


def _patch_client(module):
    base = require(module, "BaseClient")
    original = require(base, "_make_api_call")

    def _make_api_call(self, operation_name, api_params):
        if not _guarded(self) or _is_read(operation_name):
            return original(self, operation_name, api_params)

        if operation_name in _DELETES:
            return _dropped_delete(operation_name, api_params)

        if operation_name in _WRITES and mode() == "push":
            announce(
                NAME,
                "push mode — new objects may be written to the configured bucket; "
                "overwrites and deletes are refused",
            )
            if api_params.get("IfMatch"):
                block(
                    NAME,
                    f"{operation_name} {_where(api_params)}: conditional overwrite refused",
                )
            _refuse_overwrite(original, self, operation_name, api_params)
            if operation_name in _CONDITIONAL:
                api_params = {**api_params, "IfNoneMatch": "*"}
            return original(self, operation_name, api_params)

        if operation_name in _WRITES:
            reason = (
                "writes to the object store are off in local mode — set "
                "devguard.objectstore.mode = \"push\" to push new files"
            )
        else:
            reason = "only object reads and new-object writes are permitted"
        announce(NAME, "the configured object store is read-only from this bench")
        block(NAME, f"{operation_name} {_where(api_params)}: {reason}")

    base._make_api_call = mark(_make_api_call, "botocore.client.BaseClient._make_api_call")


def _patch_signers(module):
    # Each client class gets these as attributes at creation, looked up from
    # this module by name (handlers.add_generate_presigned_url/_post), so
    # replacing the module globals covers every client created afterwards.
    original_url = require(module, "generate_presigned_url")
    original_post = require(module, "generate_presigned_post")

    def generate_presigned_url(self, ClientMethod, *args, **kwargs):  # noqa: N803 - botocore's name
        if _guarded(self) and ClientMethod not in _PRESIGNABLE:
            block(
                NAME,
                f"presigned {ClientMethod} refused: only reads may be presigned, since the "
                "request would bypass this guard",
            )
        return original_url(self, ClientMethod, *args, **kwargs)

    def generate_presigned_post(self, *args, **kwargs):
        if _guarded(self):
            block(
                NAME,
                "presigned POST refused: it lets a browser write to the object store, "
                "bypassing this guard",
            )
        return original_post(self, *args, **kwargs)

    module.generate_presigned_url = mark(
        generate_presigned_url, "botocore.signers.generate_presigned_url"
    )
    module.generate_presigned_post = mark(
        generate_presigned_post, "botocore.signers.generate_presigned_post"
    )
