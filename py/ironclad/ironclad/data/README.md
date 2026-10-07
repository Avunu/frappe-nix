# ironclad/data

Everything `ironclad` reads at run time (spec S10, §1.2). It ships inside the
package, so `importlib.resources` finds the same files in the Nix build and in a
`uv tool install` from `#subdirectory=py/ironclad`. `ironclad data-path <rel>`
prints the absolute path of any of them.

| Path | Owner | What |
| --- | --- | --- |
| `schema/tool-ironclad.schema.json` | N3a, extended by N3 | JSON Schema of `[tool.ironclad]` (§2.1); its `default`s are the defaults `ironclad config` applies |
| `known-apps.json` | N3a | The sibling templates (§2.1): `{n}` is the Frappe major, `{n1}` the next one, `{name}` an `Avunu/<repo>` sibling's repo |
| `manifest.d/*.json` | N3 (`core`), N2 (`assets`), N4 (`ci`), N5 (`marketplace`) | Managed-file manifest fragments (§3.1) |
| `templates/**` | N3; `.github/**` N4; `marketplace/**` N5; `scripts/ironclad-vite-register.mjs` N2 | Jinja templates (`.j2`) and verbatim files |
| `readme/*.md.j2` | N5 | README blocks (§2.19) |
| `node-locks/version-<major>/**` | N3 | Node-lock seeds |
| `semgrep/test-correctness.yml` | N4 | The test-correctness semgrep rules |
| `ci/nightly.sh` | N4 | The nightly integration script (§4.6) |
| `shots/shots.d.ts` | N5 | Packaged copy of `lib/shots/shots.d.ts` |
