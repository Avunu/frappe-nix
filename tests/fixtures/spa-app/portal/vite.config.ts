// The portal SPA: built into spa_app/public/portal/, with a manifest, so
// scripts/vite-register.mjs copies it too under `bench build --hard-link`.
import { defineConfig } from "vite";

export default defineConfig({
	root: "portal",
	base: "/assets/spa_app/portal/",
	build: {
		outDir: "../spa_app/public/portal",
		emptyOutDir: true,
		manifest: true,
	},
});
