// Stands in for `vite build`, which writes to the same place: relative to this
// directory, two up (the harness root), then src/flow_harness/server/webui.
import { mkdirSync, writeFileSync } from "node:fs"
import { fileURLToPath, URL } from "node:url"

import { greeting } from "@flow/ui-core"

const outDir = fileURLToPath(new URL("../../src/flow_harness/server/webui", import.meta.url))
mkdirSync(outDir, { recursive: true })
writeFileSync(`${outDir}/index.html`, `<!doctype html><title>flow</title><p>${greeting}</p>\n`)
