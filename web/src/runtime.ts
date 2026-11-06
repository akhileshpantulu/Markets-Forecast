// Python engine runtime (Pyodide). Used inside a Web Worker when the page is
// hosted, and on the main thread when the single-file page is opened from disk.
// Dropped files arrive as ArrayBuffers and never leave the browser.

import type { PyodideInterface } from "pyodide";

// Engine source and configuration are bundled as text at build time.
const engineSrc = import.meta.glob("../../engine/**/*.py", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;
const configSrc = import.meta.glob(
  ["../../config/*.yaml", "../../config/markets/*.yaml", "../../config/macro/**/*.yaml", "../../data/mappings/*.yaml"],
  { query: "?raw", import: "default", eager: true },
) as Record<string, string>;

const APP_ROOT = "/home/pyodide/app";
const VENDOR = "/home/pyodide/vendor";
const PACKAGES = ["numpy", "pandas", "pydantic", "pyyaml"];

export type Msg =
  | { type: "init"; base: string; wheels: { name: string; buf: ArrayBuffer }[] }
  | { type: "run"; id: number; files: { slot: string; filename: string; buf: ArrayBuffer }[]; market: unknown; today: string;
      settings?: unknown; drivers?: unknown }
  | { type: "rerun"; id: number; settings: unknown; drivers: unknown; useFileDrivers: boolean }
  | { type: "peek"; id: number; filename: string; buf: ArrayBuffer };


function writeTree(p: PyodideInterface, files: Record<string, string>) {
  for (const [key, text] of Object.entries(files)) {
    const rel = key.replace(/^(\.\.\/)+/, "");
    const path = `${APP_ROOT}/${rel}`;
    p.FS.mkdirTree(path.slice(0, path.lastIndexOf("/")));
    p.FS.writeFile(path, text);
  }
}

export function createRuntime(post: (m: unknown) => void): (m: Msg) => Promise<void> {
  let py: PyodideInterface | null = null;

  async function init(base: string, wheels: { name: string; buf: ArrayBuffer }[]) {
    post({ type: "progress", text: "Loading the Python runtime" });
    const mod = await import(/* @vite-ignore */ `${base}pyodide.mjs`);
    const p: PyodideInterface = await mod.loadPyodide({ indexURL: base });
    post({ type: "progress", text: "Loading numpy, pandas and pydantic" });
    await p.loadPackage(PACKAGES, { messageCallback: () => {}, errorCallback: (e: string) => post({ type: "progress", text: e }) });
    post({ type: "progress", text: "Installing the forecast engine" });
    p.FS.mkdirTree(VENDOR);
    for (const w of wheels) p.unpackArchive(w.buf, "zip", { extractDir: VENDOR });
    writeTree(p, engineSrc);
    writeTree(p, configSrc);
    p.runPython(`import sys\nsys.path[:0] = ["${APP_ROOT}", "${VENDOR}"]\nimport engine.web_api`);
    py = p;
    const info = p.runPython("engine.web_api.app_info()") as string;
    const version = p.version;
    post({ type: "ready", info: JSON.parse(info), pyodide: version });
  }

  return async (m: Msg) => {
    try {
      if (m.type === "init") {
        await init(m.base, m.wheels);
        return;
      }
      if (!py) throw new Error("engine not ready");
      const api = py.pyimport("engine.web_api");
      if (m.type === "run") {
        const files = py.toPy(m.files.map((f) => ({ slot: f.slot, filename: f.filename, data: new Uint8Array(f.buf) })));
        const market = py.toPy(m.market);
        const settings = py.toPy(m.settings ?? {});
        const drivers = py.toPy(m.drivers ?? []);
        const json = api.run_ingest(files, market, m.today, true, null, settings, drivers) as string;
        files.destroy();
        market.destroy();
        settings.destroy();
        drivers.destroy();
        post({ type: "result", id: m.id, json });
      } else if (m.type === "rerun") {
        const settings = py.toPy(m.settings ?? {});
        const drivers = py.toPy(m.drivers ?? []);
        const json = api.rerun(settings, drivers, null, m.useFileDrivers) as string;
        settings.destroy();
        drivers.destroy();
        post({ type: "result", id: m.id, json });
      } else if (m.type === "peek") {
        const json = api.peek_json(new Uint8Array(m.buf), m.filename) as string;
        post({ type: "peek", id: m.id, json });
      }
      api.destroy();
    } catch (e) {
      post({ type: "error", id: (m as { id?: number }).id, message: e instanceof Error ? e.message : String(e) });
    }
  };
}
