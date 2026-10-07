# ironclad

The Python side of the Ironclad platform in frappe-nix: `ironclad <command>`.
The contract is [docs/ironclad/spec.md](../../docs/ironclad/spec.md).

- In an app's dev shell, `ironclad` is on `PATH` (built by `lib/ironclad/package.nix`).
- In CI without Nix, install it at the frappe-nix revision the app's `flake.lock` pins:

  ```
  uv tool install "ironclad @ git+https://github.com/Avunu/frappe-nix@<rev>#subdirectory=py/ironclad"
  ```

Every data file the commands read (templates, manifest fragments, the
`[tool.ironclad]` schema, `known-apps.json`, …) lives under `ironclad/data/`
and is read through `importlib.resources`, so both installs see the same files.

## Layout

| Path | What |
| --- | --- |
| `ironclad/cli.py` | The dispatcher. It imports every module in `ironclad/commands/`. |
| `ironclad/commands/<name>.py` | One module per command family. Each defines `register(subparsers)`. |
| `ironclad/common/` | Shared helpers: the repo, `flake.lock`, `[tool.ironclad]`, known apps, pins, NAR hashing, `gh api`, reports. |
| `ironclad/data/` | Everything read at run time. |
| `tests/test_<module>.py` | `unittest` suites; the Nix build runs them. |

Run the tests from this directory with `python -m unittest discover -s tests`.
