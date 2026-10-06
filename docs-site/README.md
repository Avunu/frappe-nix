# Documentation site

This folder builds the project's documentation site from the Markdown in `../docs/` and publishes it to GitHub Pages at the project's own address. It is a [Jx](https://jxsuite.com) project, shared by every Avunu open-source repository: the same look as [avunu.net](https://avunu.net), a switcher to jump to the other projects, and nothing to configure beyond `docs.config.json`.

You write Markdown in `docs/`. You do not edit anything in this folder unless you want to change how every page looks.

## Commands

Run them from this folder. They need [Bun](https://bun.sh) 1.4 or newer.

| Command                  | What it does                                                                                                                           |
| ------------------------ | -------------------------------------------------------------------------------------------------------------------------------------- |
| `bun install`            | Installs the dependencies and generates the editor schemas.                                                                            |
| `bun run dev`            | Dev server with live reload; the sidebar follows `docs/` while it runs. `bun run dev --port 3417` when port 3000 is taken.             |
| `bun run build`          | Production build into `dist/`. `--lenient` turns document problems into warnings.                                                      |
| `bun run check`          | What CI runs: set-up check, tests, colour contrast, a strict build and a link check of the built site.                                 |
| `bun run lint:docs`      | Only the Markdown checks (what the site cannot render), with file and line.                                                            |
| `bun test`               | The unit tests of the scripts and of the project's own JSON.                                                                           |
| `bun run nav -- --print` | Stages `docs/`, writes `.generated/nav.json` and prints the sidebar the build would produce.                                           |
| `bun run sync:projects`  | Refreshes the project list of the switcher from `https://avunu.net/projects.json`.                                                     |
| `bun scripts/init.ts`    | Fills in `docs.config.json`, the address and the workflow (once, after copying this folder in; again after an update, with `--force`). |
| `bun run link:jx <path>` | Uses a local Jx checkout instead of the npm packages (see "Jx version").                                                               |

## How the site is built

1. `scripts/lib/stage.ts` copies `docs/` to `.generated/docs/`, the folder the `docs` content type reads, and fixes what Jx cannot: links and images that point at a file of the repository outside `docs/` become GitHub links, and a copyright comment above the frontmatter is moved behind it. Each rewritten link is listed in the build output (`stage: docs/README.md:12 worker/README.md -> https://github.com/…`).
2. `scripts/lib/lint.ts` reads `docs/` and reports Markdown the site cannot render (see "What does not render"). Errors fail a strict build; warnings are printed.
3. `scripts/nav.ts` reads the staged copy and writes `.generated/nav.json`: the sidebar, each page's title and description, previous and next links.
4. `jx build` turns each Markdown file into a page at `/docs/<path>/` and the search index. `project.json` says how: the `docs` content type reads `./.generated/docs`, maps GitHub alerts to `components/docs-callout.json`, rewrites relative links between pages and leaves out drafts.
5. `scripts/postbuild.ts` fixes what Jx does not: whitespace in the HTML, canonical addresses, the sitemap, search titles, the `<title>` text, links from raw HTML to files of the repository, the Markdown copies Jx writes beside every page, and `404.html`.
6. `.github/workflows/docs.yml` (at the repository root) runs `bun run check` on every pull request and publishes `dist/` to GitHub Pages from the repository's default branch.

Pages ship almost no JavaScript. Only the project menu, the search, the theme toggle and the small script that adds copy buttons, heading anchors and the drawer's focus handling run in the browser.

## Writing the docs

Everything below works on GitHub, in Obsidian and on the site at once, except where "What does not render" says otherwise.

### Files and folders

- `docs/README.md` (or `docs/index.md`) is the documentation home (`/docs/`) and is required.
- Every other `.md` file is a page at `/docs/<path>/`: `docs/Install Steps.md` is `/docs/install-steps/`. A folder is a section of the sidebar; its `README.md` (or `index.md`) is the section's "Overview".
- Files and folders that start with `.` or `_` are ignored. So is `node_modules`.
- The folder is always `docs/`, at the repository root, beside this folder.
- Images and downloads go anywhere under `docs/` and are linked relatively: `![Diagram](images/flow.png)`.

### Frontmatter

Frontmatter is optional. Without it a page is titled by its first `# Heading` (or its file name) and described by its first paragraph.

| Key           | Meaning                                                                          |
| ------------- | -------------------------------------------------------------------------------- |
| `title`       | The page title. Wins over the first heading.                                     |
| `description` | One sentence under the title, in search results and in link previews.            |
| `nav_title`   | A shorter name for the sidebar.                                                  |
| `order`       | A number. Lower comes first; pages without one come last, alphabetically.        |
| `hidden`      | `true` publishes the page but leaves it out of the sidebar and of previous/next. |
| `draft`       | `true` keeps the page out of the site entirely. `publish: false` does the same.  |
| `updated`     | A date (`2026-10-01`), shown under the title.                                    |
| `tags`        | A list, shown under the title.                                                   |

A field of the wrong type (`draft: "yes"`, `order: "2"`) is reported by the build and fails it in CI. Write `true`, not `"yes"`.

The title is shown once, as the page's `h1`. The first `# Heading` that says the same as the title (ignoring case and punctuation) is that `h1`, wherever it stands in the file (after a badge, a comment or a logo), and links to its anchor keep working. Every other level-1 heading is shown as a section heading (`h2`), so a page always has exactly one `h1`. A comment above the frontmatter (`<!-- Copyright … -->`, which some pre-commit hooks add) is tolerated by the build, but GitHub and Obsidian then show the frontmatter as text: keep `docs/` out of such hooks.

### Callouts, links and code

````markdown
> [!NOTE]
> Alerts use GitHub's notation: NOTE, TIP, IMPORTANT, WARNING and CAUTION.

> [!TIP] A custom title
> The Obsidian form with a title works too.

See [Configuration](guides/configuration.md#options) and [Install Steps](Install%20Steps.md).

```bash
bun run dev
```
````

- Link to other pages by file, relative to the file you are in, with `%20` for a space. The site rewrites them to the page's address, and a link to a heading uses the heading's anchor.
- A link or image to a file of the repository that is not a page (a source file, `LICENSE`, another README, a folder of examples) becomes a link to it on GitHub, whether you wrote it from `docs/` (`../worker/README.md`) or from the repository root, as in a README copied into `docs/` (`worker/README.md`). A link written from the root to a page in `docs/` (`docs/chat.md`) is made relative to the file you are in.
- A link to a page that does not exist, or that is a draft, is shown as plain text and **fails the CI build** with the file and the target named. Use `--lenient` while you fix them.
- Every code block needs a language. These are highlighted, in light and dark: `json`, `jsonc`, `js`/`javascript`, `ts`/`typescript`, `md`/`markdown`, `html`, `css`, `bash`/`sh`/`shell`, `yaml`/`yml`, `sql`, `php`, `python`/`py`, `ruby`/`rb`, `nix`, `nginx`, `caddyfile`, `toml`, `ini`, `diff`, `xml` and `dockerfile`. Any other language (`rust`, `go`, `vue`, `dotenv`, `console`, `jinja`, `scss`) is shown as plain code, and the build lists which pages have such blocks. A block without a language is labelled "Text".
- Write placeholders as `<UPPER_SNAKE_CASE>` and use `example.com` for domains. Never paste real credentials or customer data.

### What does not render

Jx drops or reshapes a few constructs that GitHub and Obsidian handle. The build reports each one with `docs/<file>:<line>` and what to write instead; the ones marked "error" lose text, so they fail a strict build.

| Written                                                     | What the site does                   | Build       |
| ----------------------------------------------------------- | ------------------------------------ | ----------- |
| Reference-style links (`[text][ref]` and `[ref]: url`)      | The text of every such link vanishes | error in CI |
| Footnotes (`[^1]` and `[^1]: note`)                         | The marker and the note vanish       | error in CI |
| Inline HTML (`<kbd>Ctrl</kbd>`, `<b>`, `<sub>`, `<a href>`) | The text stays, outside the element  | warning     |
| `<a>` around an `<img>` in a paragraph (badges)             | The image stays, the link is lost    | warning     |
| A `<div>` or `<details>` with a blank line inside           | An empty element, then the content   | warning     |
| Task lists (`- [ ]`)                                        | Plain list items                     | warning     |
| Table column alignment (`:--:`)                             | Columns are left-aligned             | warning     |
| `${...}` in a link address                                  | Jx evaluates it and drops the link   | warning     |

Block HTML that does not have a blank line in it (`<p align="center"><img …></p>`) works, as do `<br>`, `<img>` and HTML comments. Write `[![Build](badge.svg)](https://…)` for a badge.

## Settings: `docs.config.json`

| Key        | Meaning                                                                                                                                                               |
| ---------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `name`     | Shown in the header, the title and the footer.                                                                                                                        |
| `tagline`  | One sentence on the landing page and in search results.                                                                                                               |
| `slug`     | The project's slug in `https://avunu.net/projects.json` (may have underscores): marks it in the menu.                                                                 |
| `platform` | `frappe`, `odoo`, `wordpress`, `nixos` or `general`.                                                                                                                  |
| `repo`     | The GitHub repository URL.                                                                                                                                            |
| `domain`   | The custom domain: the repository name with hyphens, then `.avunu.net`.                                                                                               |
| `license`  | An SPDX identifier, shown on the landing page and in the footer.                                                                                                      |
| `branch`   | Optional, default `main`: the repository's default branch. "Edit this page" and GitHub links use it, and the workflow publishes from it (`init` writes it there too). |

`bun scripts/init.ts` writes this file, `project.json` (name and address) and `public/CNAME` together. The build checks that they agree, and that `slug` is the catalog's spelling.

## The project menu

The header's **Projects** button lists Avunu's open-source projects by platform, marks this one, and links each to its own docs site when it has one and to its page on avunu.net when it does not. It ends with **All open-source projects** and **Back to avunu.net**. It is a list of links that opens and closes like any disclosure: Enter or Space opens it, Tab walks the links (and out of the list, which closes it), Escape closes it.

The list is baked into every page from `data/projects.snapshot.json`, so the menu works offline and when avunu.net is down. When the page loads it fetches the live list from `https://avunu.net/projects.json` and swaps it in (never while the menu is open). `bun run sync:projects` refreshes the snapshot; CI does it on every build and keeps the committed one if avunu.net cannot be reached. A project appears with its docs link once its entry in avunu.net's catalog has a `docs` address. Until avunu.net publishes `/projects.json`, the browser console shows one failed request for it on every page: that is expected.

## Jx version

The site needs the Jx release that includes the vault-content features (GitHub alerts, relative link resolution, `exclude`/`where`/`route` for content types): `@jxsuite/parser` 2.0.0 or newer together with the compiler, search and schema releases cut with it ([jxsuite/jx pull request 426](https://github.com/jxsuite/jx/pull/426)). `bun run build` stops with a message when the installed parser is older.

Until that release is published, use a checkout of Jx at the commit your maintainers use (the repository variable `DOCS_JX_REF`):

```bash
git clone https://github.com/jxsuite/jx /tmp/jx
git -C /tmp/jx checkout <the commit in DOCS_JX_REF>
(cd /tmp/jx && bun install && bun run build)
bun install && bun run link:jx /tmp/jx
```

Run `bun run link:jx` again after every `bun install`, which restores the npm copies (it links `@jxsuite/compiler`, `parser`, `search` and `server`). In CI, set the repository variable `DOCS_JX_REF` to the **full commit SHA** and the workflow does the same (a branch or a tag is refused). When the release is out, delete `DOCS_JX_REF`, run `bun update @jxsuite/parser @jxsuite/compiler @jxsuite/search @jxsuite/runtime @jxsuite/server` and commit `bun.lock`.

## Publishing

The workflow does nothing until the repository variable `DOCS_SITE_ENABLED` is `true`. A maintainer does these once, ideally before merging the pull request that adds the site:

1. Settings, Pages, Source: **GitHub Actions**; Custom domain: the `domain` of `docs.config.json`.
2. In DNS, a `CNAME` from that domain's first label to `avunu.github.io`, **DNS only** until GitHub has issued the certificate; then tick **Enforce HTTPS**.
3. Settings, Secrets and variables, Actions, Variables: `DOCS_SITE_ENABLED` = `true` (and `DOCS_JX_REF`, see above).
4. Merge. Every push to the default branch that touches `docs/` or this folder deploys; Actions, Docs, **Run workflow** repeats a deployment.

The branch in the workflow's trigger is the one `init` found as the repository's default branch. If the default branch is renamed or changes (an Odoo repository moving from `18.0` to `19.0`), run `bun scripts/init.ts --branch <new branch> --force` from this folder and commit `.github/workflows/docs.yml` and `docs.config.json`.

## Updating from the starter

The look, the components and the scripts come from Avunu's shared project docs starter (ask an Avunu maintainer for a checkout). To take a newer version of its `template/` folder, from the repository root, with `STARTER` the starter's folder in that checkout:

```bash
STARTER=/path/to/project-docs-starter
NEW=$(mktemp -d) && git -C "$STARTER" archive HEAD template | tar -x -C "$NEW" --strip-components=1
# what differs (files that are only in docs-site/ are yours, or no longer in the template)
diff -rq --exclude=node_modules --exclude=dist --exclude=.generated --exclude=bun.lock "$NEW" docs-site
# copy everything except what is yours: docs.config.json, public/CNAME and the catalog snapshot
(cd "$NEW" && tar --exclude=./docs.config.json --exclude=./public/CNAME --exclude=./data -cf - .) | tar -x -C docs-site
cd docs-site && bun scripts/init.ts --force && bun install && bun run check
```

`init` keeps what `docs.config.json` says, writes the name and address back into `project.json` and `package.json`, and with `--force` replaces the workflow at `.github/workflows/docs.yml` with the new one (look at `git diff` if you changed it). An existing `.github/dependabot.yml` is never replaced.

## Troubleshooting

- **"docs.config.json still has the template values"**: run `bun scripts/init.ts` (with `--name`, `--tagline` and `--license` when the catalog does not list the repository).
- **"slug … is not in the project catalog, but … is"**: the slug has the catalog's spelling, with its underscores (`erpnext_taskview`). Set `slug` in `docs.config.json`; the domain keeps its hyphens.
- **"predates the vault-content features"**: see "Jx version".
- **"the sidebar links to /docs/x/, which was not built"**: the file name produces an address that `scripts/lib/slug.ts` and Jx disagree on. Rename the file to plain letters, digits, spaces and hyphens, or report it.
- **A link is plain text in the page**: the build named it. The target does not exist, is a draft, or is not in the repository. Links to files of the repository outside `docs/` are rewritten to GitHub, so this is a typo or a draft.
- **`lint: error: docs/…`**: reference-style links or footnotes, which the site cannot show. Write the links inline (`[text](url)`) and put a footnote in the sentence.
- **"N `<h1>` elements"** from the link check: the page template shows one `h1` and demotes the others, so this means an `h1` the template cannot reach (inside an element Jx drops). Replace the raw HTML with Markdown, or report the page.
- **`bun run check` fails only in CI**: CI builds strictly (`CI=true`). Run `bun run check` locally, which does the same.
- **On NixOS the build stops with `Sharp is required for image optimization but failed to load` (`libstdc++.so.6`)** as soon as `docs/` has an image: `sharp`'s native module cannot find the C++ library. Provide it for the command: `LD_LIBRARY_PATH="$(nix eval --raw nixpkgs#stdenv.cc.cc.lib.outPath)/lib" bun run build`. Ubuntu CI is not affected.
- **`bun run dev` says the port is in use**: `bun run dev --port 3417`.
- **The dev server rebuilds `dist/`** (Jx dev writes there too); run `bun run build` again before looking at `dist/`.
- **The repository's pre-commit hooks rewrite this folder** (a linter without a path filter, a hook that stamps a copyright line): exclude `docs-site/` from them, and `docs/` from the hook that touches Markdown.
