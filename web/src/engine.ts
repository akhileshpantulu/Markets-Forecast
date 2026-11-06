// Main-thread bridge to the Python engine worker.
import EngineWorker from "./worker.ts?worker&inline";
import openpyxlUrl from "./wheels/openpyxl-3.1.5-py2.py3-none-any.whl?url";
import etxmlUrl from "./wheels/et_xmlfile-2.0.0-py3-none-any.whl?url";

export const PYODIDE_VERSION = "314.0.7";
const CDN_BASE = `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`;

export interface Slot { id: string; label: string; hint: string; required: boolean; enabled: boolean; accept?: string }
export interface MarketInfo { slug: string; name: string; currency: string; costar_market: string | null; costar_submarket: string | null }
export interface AppInfo { engine_version: string; slots: Slot[]; markets: MarketInfo[] }
export type MarketChoice = { slug: string } | { adhoc: { name: string; currency: string; costar_market?: string; costar_submarket?: string } };

/** Pyodide base URL. A ?pyodide= override is honoured only for a localhost URL (used by tests). */
function pyodideBase(): string {
  const o = new URLSearchParams(location.search).get("pyodide");
  if (o) {
    try {
      const u = new URL(o);
      if (u.hostname === "localhost" || u.hostname === "127.0.0.1") return u.href.endsWith("/") ? u.href : `${u.href}/`;
    } catch { /* ignore */ }
  }
  return CDN_BASE;
}

/** Minimal Worker-like port. */
interface Port { postMessage(m: unknown, transfer?: Transferable[]): void; onmessage: ((ev: { data: any }) => void) | null; onerror: ((e: { message: string }) => void) | null }

/**
 * Pages opened from disk (file://) cannot start blob-URL module workers in
 * Chromium, so the single-file build runs Python on the main thread there.
 * The runtime module is loaded only in that case.
 */
function inPagePort(): Port {
  const port: Port = { onmessage: null, onerror: null, postMessage: () => {} };
  const ready = import("./runtime").then(({ createRuntime }) => createRuntime((m) => setTimeout(() => port.onmessage?.({ data: m }), 0)));
  port.postMessage = (m) => { void ready.then((handle) => handle(m as never)); };
  return port;
}

export class Engine {
  private w: Port;
  private seq = 0;
  private pending = new Map<number, { resolve: (v: string) => void; reject: (e: Error) => void }>();
  ready: Promise<{ info: AppInfo; pyodide: string }>;

  constructor(onProgress: (text: string) => void) {
    this.w = location.protocol === "file:" ? inPagePort() : (new EngineWorker() as unknown as Port);
    this.ready = new Promise((resolve, reject) => {
      this.w.onmessage = (ev: { data: any }) => {
        const m = ev.data;
        if (m.type === "progress") onProgress(m.text);
        else if (m.type === "ready") resolve({ info: m.info, pyodide: m.pyodide });
        else if (m.type === "result" || m.type === "peek") this.pending.get(m.id)?.resolve(m.json);
        else if (m.type === "error") {
          if (m.id !== undefined && this.pending.has(m.id)) this.pending.get(m.id)!.reject(new Error(m.message));
          else reject(new Error(m.message));
        }
        if (m.id !== undefined && (m.type === "result" || m.type === "peek" || m.type === "error")) this.pending.delete(m.id);
      };
      this.w.onerror = (e: { message: string }) => reject(new Error(e.message || "the engine worker could not start"));
    });
    void this.start();
  }

  private async start() {
    const wheels = await Promise.all(
      [["openpyxl", openpyxlUrl], ["et_xmlfile", etxmlUrl]].map(async ([name, url]) => ({
        name,
        buf: await (await fetch(url)).arrayBuffer(),
      })),
    );
    this.w.postMessage({ type: "init", base: pyodideBase(), wheels }, wheels.map((x) => x.buf));
  }

  private call(msg: Record<string, unknown>, transfer: Transferable[]): Promise<string> {
    const id = ++this.seq;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.w.postMessage({ ...msg, id }, transfer);
    });
  }

  async run(files: { slot: string; file: File }[], market: MarketChoice, settings: Record<string, unknown> = {}, drivers: unknown[] = []): Promise<unknown> {
    const payload = await Promise.all(files.map(async (f) => ({ slot: f.slot, filename: f.file.name, buf: await f.file.arrayBuffer() })));
    const today = new Date().toISOString().slice(0, 10);
    const json = await this.call({ type: "run", files: payload, market, today, settings, drivers }, payload.map((p) => p.buf));
    return JSON.parse(json);
  }

  /** Rerun only the forecast on the data already loaded, with new settings and extra drivers. */
  async rerun(settings: Record<string, unknown>, drivers: unknown[], useFileDrivers = true): Promise<unknown> {
    return JSON.parse(await this.call({ type: "rerun", settings, drivers, useFileDrivers }, []));
  }

  async peek(file: File): Promise<{ market?: string[]; submarket?: string[]; country?: string[] }> {
    const buf = await file.arrayBuffer();
    return JSON.parse(await this.call({ type: "peek", filename: file.name, buf }, [buf]));
  }
}
