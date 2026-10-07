# frappe_nix_tools/data

Everything `frappe-nix` reads at run time (spec S10, §1.2). It ships inside the
package, so `importlib.resources` finds the same files in the Nix build and in a
`uv tool install` from `#subdirectory=py/frappe_nix_tools`. `frappe-nix data-path <rel>`
prints the absolute path of any of them.

| Path | Owner | What |
| --- | --- | --- |
| `profiles/minimal.toml`, `profiles/recommended@<minor>.toml` | N3a, then N3 | The built-in profiles (§8.5). `recommended` snapshots are frozen once released (S42); a minor that changes `recommended` adds a new file |
| `schema/profile.schema.json` | N3a, extended by N3 | JSON Schema of a profile (§8.1); the module definitions, whose `default`s are the values a key gets when no layer sets it. `x-app-only` marks parameters only an app may set |
| `schema/tool-frappe-nix.schema.json` | N3a, extended by N3 | JSON Schema of `[tool.frappe-nix]` (§2.1): the app keys, plus the module tables by reference to the profile schema |
| `known-apps.json` | N3a | The sibling templates (§2.1): `{n}` is the Frappe major, `{n1}` the next one; `*/*` is the generic rule for any `<owner>/<repo>` (`{owner}`, `{name}`) |
| `manifest.d/*.json` | N3 (`core`), N2 (`assets`), N4 (`ci`), N5 (`marketplace`) | Managed-file manifest fragments (§3.1) |
| `templates/**` | N3; `.github/**` N4; `marketplace/**` N5; `scripts/vite-register.mjs` N2 | Jinja templates (`.j2`) and verbatim files |
| `readme/*.md.j2` | N5 | README blocks (§2.19) |
| `node-locks/version-<major>/**` | N3 | Node-lock seeds |
| `semgrep/test-correctness.yml` | N4 | The test-correctness semgrep rules |
| `ci/nightly.sh` | N4 | The nightly integration script (§4.6) |
| `shots/shots.d.ts` | N5 | Packaged copy of `lib/shots/shots.d.ts` |
