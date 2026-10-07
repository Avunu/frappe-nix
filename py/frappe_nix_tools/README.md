# frappe-nix-tools

The Python side of frappe-nix's app standards: the `frappe-nix <command>` CLI
(import name `frappe_nix_tools`). The contract is
[docs/app-standards/spec.md](../../docs/app-standards/spec.md).

- In an opted-in app's dev shell, `frappe-nix` is on `PATH` (built by `lib/standards/package.nix`).
- In CI without Nix, install it at the frappe-nix revision the app's `flake.lock` pins:

  ```
  uv tool install "frappe-nix-tools @ git+https://github.com/Avunu/frappe-nix@<rev>#subdirectory=py/frappe_nix_tools"
  ```

Every data file the commands read (the built-in profiles, the schemas,
`known-apps.json`, and later the templates and manifest fragments) lives under
`frappe_nix_tools/data/` and is read through `importlib.resources`, so both
installs see the same files.

## Layout

| Path | What |
| --- | --- |
| `frappe_nix_tools/cli.py` | The dispatcher. It imports every module in `frappe_nix_tools/commands/`. |
| `frappe_nix_tools/commands/<name>.py` | One module per command family. Each defines `register(subparsers)`. |
| `frappe_nix_tools/common/` | Shared helpers: the repo, `flake.lock`, `[tool.frappe-nix]`, the resolved configuration (`config.py`), schema validation, known apps, pins, NAR hashing, `gh api`, reports. |
| `frappe_nix_tools/data/` | Everything read at run time. |
| `tests/test_<module>.py` | `unittest` suites; the Nix build runs them. |

Run the tests from this directory with `python -m unittest discover -s tests`.
