import { defineConfig } from "vite";
import { viteSingleFile } from "vite-plugin-singlefile";

// Two builds from the same code:
//   default: a static site for GitHub Pages or Vercel (web/dist)
//   single:  one self-contained HTML file for local use (web/dist-single)
// Neither contains any data. Python runs in the browser through Pyodide,
// which is loaded from the jsDelivr CDN at a pinned version.
export default defineConfig(({ mode }) => ({
  base: "./",
  server: { fs: { allow: [".."] } },
  worker: { format: "es" },
  plugins: mode === "single" ? [viteSingleFile({ removeViteModuleLoader: true })] : [],
  build: {
    outDir: mode === "single" ? "dist-single" : "dist",
    emptyOutDir: true,
    target: "es2022",
    assetsInlineLimit: mode === "single" ? 100_000_000 : 4096,
    chunkSizeWarningLimit: 2000,
  },
}));
