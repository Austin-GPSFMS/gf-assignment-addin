import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

/**
 * Vite config for the GPSFMS "My General Foreman" Geotab Drive add-in.
 *
 * Hosted at:
 *   https://austin-gpsfms.github.io/gf-assignment-addin/dist/index.html
 *
 * `base` is the ABSOLUTE GitHub Pages URL, same convention as
 * recall-harness-swap-addin / visiontrack_addin: Geotab Drive injects the
 * add-in HTML into its own page (not an iframe), so relative asset paths
 * would resolve against Geotab's origin and 404. Change it here if the
 * add-in moves hosts.
 *
 * Target is ES2020 (not ES2022) because the add-in runs inside the Geotab
 * Drive WebView on older Android tablets.
 */
export default defineConfig({
  base: "https://austin-gpsfms.github.io/gf-assignment-addin/dist/",
  plugins: [react()],
  build: {
    target: "es2020",
    outDir: "dist",
    assetsDir: "assets",
    sourcemap: true,
    assetsInlineLimit: 20480,
    rollupOptions: {
      output: {
        entryFileNames: "assets/[name]-[hash].js",
        chunkFileNames: "assets/[name]-[hash].js",
        assetFileNames: "assets/[name]-[hash].[ext]",
      },
    },
  },
  server: { port: 5175, strictPort: false },
});
