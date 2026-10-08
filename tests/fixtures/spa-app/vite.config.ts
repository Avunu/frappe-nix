// The desk bundle: what an app's root Vite config looks like under
// docs/app-standards/assets.md. The tests run build.mjs instead, which writes
// the same files without node_modules or the network.
import { defineConfig } from "vite";

export default defineConfig({
	build: {
		outDir: "spa_app/public/dist",
		emptyOutDir: false,
		manifest: true,
		rollupOptions: {
			input: { spa: "spa_app/public/js/spa/spa.entry.ts" },
			output: {
				entryFileNames: "js/[name].bundle.[hash].js",
				chunkFileNames: "js/[name]-[hash].js",
				assetFileNames: "css/[name].bundle.[hash][extname]",
			},
		},
	},
});
