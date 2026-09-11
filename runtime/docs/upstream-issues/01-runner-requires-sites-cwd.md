# `frappe.runner` only works if started from `sites/`

**Branch:** `develop` (verified at `34224e0128`, 2026-09-10) **Component:** `frappe/runner.py` **Impact:** started from the bench root the process fails to boot; if it gets past that, realtime silently misconfigures and every background job fails.

## What happens

`python -m frappe.runner` from the bench root — the natural place, and where `bench`'s own CLI resolves its bench from — does not work. Three distinct failures, in the order you meet them:

1.  **Startup dies on handler discovery.**
    
    ```
    File "frappe/realtime/registry.py", line 114, in discover_app_handlers
        for app in frappe.get_all_apps(with_internal_apps=False, sites_path=sites_path):
    OSError: b'/path/to/bench/apps.txt' Not Found
    ```
    
    `sites_path` resolved to the bench root, so `apps.txt` is looked for one level too high.
    
2.  **Realtime silently loses its configuration.** Past that, `get_config()` reads `common_site_config.json` relative to the same wrong path. Nothing is found and every value falls back to its default — `socketio_port`, `socketio_uds`, `default_site`. `_get_common_site_config` returns an empty dict for a missing file rather than raising, so there is no warning at all. (`redis_queue` survives only if `FRAPPE_REDIS_QUEUE` is set, via `_apply_common_env_overrides`.)
    
3.  **Every background job fails.**
    
    ```
    File "frappe/utils/background_jobs.py", line 255, in execute_job
        frappe.init(site, force=True, is_job=True)
    File "frappe/config.py", line 59, in _get_site_config
        raise IncorrectSitePath(error_msg)
    frappe.exceptions.IncorrectSitePath: 404 Not Found: mysite.localhost does not exist.
    ```
    

## Reproduction

```sh
cd /path/to/bench          # NOT sites/
python -m frappe.runner
```

## Root cause

`frappe.init`'s signature is `init(site, sites_path=".", ...)` (`frappe/__init__.py:284`), so `sites_path` is the cwd unless told otherwise. Two callers rely on the cwd already being `sites/`:

-   `frappe/runner.py:118` — `TrafficMiddleware.load()` calls `frappe.init(site="")`, which sets `frappe.local.sites_path` to the cwd. `frappe/realtime/config.py:51` then resolves its own `sites_path` from `frappe.local.sites_path`, and `discover_app_handlers` passes it to `frappe.get_all_apps`.
-   `frappe/utils/background_jobs.py:255` — `execute_job` re-inits per job, also defaulting to the cwd. Nothing the runner does can correct this from outside.

The realtime server's own entry point already handles it:

```python
if os.path.isdir("sites"):
    os.chdir("sites")
```

(`frappe/realtime/server.py:159-160`). `runner.main()` (`frappe/runner.py:504`) has no equivalent — the newer entry point lost the behaviour its sibling has.

## Suggested fix

Give `runner.main()` the same chdir, guarded on the directory so a re-exec (which keeps the cwd) does not descend twice. That alone fixes all three symptoms.

Independently, `frappe.init(site="")` at `runner.py:118` would be more robust resolving `sites_path` from `SITES_PATH`, the way `frappe/app.py:35` and `frappe.get_conf` (`frappe/config.py:162`) already do — then the process is correct wherever it is started, and consistent with how the rest of the framework resolves the same thing.

Worth considering separately: `_get_common_site_config` returning `{}` for a missing file is what turns symptom 2 into a silent one.

## Workaround

`patches/0003-runner-sites-path.patch` and `patches/0005-runner-chdir-sites.patch` in [https://github.com/Avunu/frappe-nix/tree/main/runtime](https://github.com/Avunu/frappe-nix/tree/main/runtime).
