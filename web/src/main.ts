import "./styles.css";
import { Engine, PYODIDE_VERSION, type AppInfo, type MarketChoice, type Slot } from "./engine";
import { disposeCharts, ledgerChart, type Band } from "./charts";
import { download, h, num, pct, signedPct } from "./format";
import { applyCached, geocode, renderMap, STAGE_COLOR, type Site } from "./map";

// ---------- result types (engine/web_api.py and engine/ledger/run.py) ----------
interface Check { id: string; level: string; status: "pass" | "warn" | "fail" | "info"; title: string; message: string; details: string[] }
interface Line { id: string; layer: string; title: string; occ: number; adr: number; text: string; basis: string; evidence: string }
interface Year {
  year: number; label: string; is_stub: boolean; lines: Line[]; occ_growth: number; adr_growth: number; revpar_growth: number;
  occupancy: number; adr: number; revpar: number;
  range: { occ_growth: Band; adr_growth: Band; revpar_growth: Band; occupancy: Band; adr: Band; revpar: Band; p_revpar_fall: number };
}
interface Forecast {
  meta: { engine_version: string; seed: number; paths: number; stub_year: number | null; base_year: number; last_actual_month: string;
    currency: string; registry_version: string; calibrated_on: string | null; panel_snapshot: string | null; provisional: boolean; seconds: number };
  baseline: { start: number; end: number; revpar: number; occupancy: number; adr: number; supply: number; demand: number; cpi_mean: number | null; split: string };
  years: Year[];
  five_year: { from: number; to: number; revpar_cagr: number; p10: number; p90: number; baseline_revpar_cagr: number; difference: number };
  supply: { inventory_trend: number; base_rooms: number; average_tier: string; notes: string[];
    years: { year: number; supply_growth: number; inventory_growth: number; excess_vs_trend: number; new_builds_rooms: number; reopening_rooms: number;
      unannounced_rooms: number; removed_rooms: number; adr_mix_index: number; overlap: number }[];
    projects: { name: string; rooms: number; stage: string; tier: string; product: string | null; p_open_horizon: number; median_open: string | null }[] };
  drivers: { applied: { id: string; title: string; affects: string; probability: number; status: string; calculator: string | null; working: string[] }[];
    issues: { id: string; level: string; text: string }[]; ladder: Record<string, { default: number }>; cards: any[] };
  ranges: { source: string | null; volatility_ratio: { occ: number; adr: number }; note: string };
  calibration: { version: string; overridden: string[]; evidence: Record<string, any> };
  adjustments: string[]; gaps: string[];
  history: { year: number; occupancy: number; adr: number; revpar: number; complete: boolean }[];
  report_md: string;
  coverage?: { rows: { id: string; owner: string; title: string; status: string; cards: string[]; finding: string; sources?: { publisher?: string; url?: string }[] }[];
    issues: { id: string; level: string; text: string }[]; answered: number; items: number } | null;
}
interface Result {
  meta: { run_id: string; engine_version: string; market: string; slug: string; currency: string; passed: boolean; date: string;
    first_month: string | null; last_month: string | null; stub_year: number | null; stub_actual_months: number | null };
  fatal: string[];
  files: { category: string; filename: string; mapping: string | null; sheet: string | null; rows: number; snapshot_date: string | null; note: string }[];
  checks: Record<string, Check[]>;
  breaks: { measure: string; description: string; action: string }[];
  validation_md: string;
  forecast?: Forecast;
  forecast_error?: string;
  drivers_error?: string;
  settings_schema?: SettingGroup[];
  research_brief?: string;
  pipeline_map?: { sites: Site[]; current_supply: number | null; as_of: string; country: string };
  pipeline_map_error?: string;
}
interface SettingField { key: string; label: string; kind: string; min?: number; max?: number; step?: number; default: number | boolean | string | null;
  options?: [string, string][]; status?: string; evidence?: string }
interface SettingGroup { id: string; title: string; help: string; fields: SettingField[] }

// ---------- state ----------
const state = {
  info: null as AppInfo | null,
  files: new Map<string, File>(),
  market: "" as string,
  other: { name: "", currency: "EUR", market: "", submarket: "" },
  result: null as Result | null,
  tab: "forecast",
  metric: "revpar" as "revpar" | "occupancy" | "adr",
  settings: {} as Record<string, unknown>,
  customDrivers: [] as any[],
  driverEdits: {} as Record<string, { enabled?: boolean; probability?: number }>,
  fileCards: [] as any[],
  rerunning: false,
  running: false,
  map: null as null | { key: string; canvas: HTMLCanvasElement; tiles: number },
  geocoding: "" as string,
  exporting: false,
  error: "",
};

const app = document.getElementById("app")!;
const statusEl = h("span", { class: "status", role: "status", "aria-live": "polite" }, h("span", { class: "dot" }), "Starting");
const inputsEl = h("aside", { class: "inputs", "aria-label": "Inputs" });
const resultsEl = h("section", { class: "results", "aria-label": "Results" });

function setStatus(text: string, cls = "") {
  statusEl.className = `status ${cls}`;
  statusEl.replaceChildren(h("span", { class: "dot" }), text);
}

function toggleTheme() {
  const root = document.documentElement;
  const dark = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
  root.dataset.theme = dark ? "light" : "dark";
  try { localStorage.setItem("theme", root.dataset.theme); } catch { /* storage unavailable */ }
  if (state.result) renderResults();
}

app.append(
  h("header", { class: "top" }, h("h1", {}, "Hotel Market Forecast"), statusEl, h("span", { class: "spacer" }),
    h("button", { class: "btn", type: "button", onclick: toggleTheme, "aria-label": "Switch light or dark theme" }, "Theme")),
  h("main", { class: "layout" }, inputsEl, resultsEl),
);

const engine = new Engine((t) => setStatus(`${t}…`));
engine.ready
  .then(({ info, pyodide }) => {
    state.info = info;
    if (!state.market && info.markets.length) state.market = info.markets[0].slug;
    setStatus(`Engine ready. Runs in this browser (Pyodide ${pyodide}, engine ${info.engine_version})`, "ready");
    renderInputs();
  })
  .catch((e: Error) => setStatus(`Engine failed to load: ${e.message}`, "error"));

// ---------- inputs ----------
const DEFAULT_SLOTS: Slot[] = [
  { id: "performance", label: "Market performance", hint: "CoStar DataTable export, monthly", required: true, enabled: true },
  { id: "pipeline", label: "Pipeline", hint: "CoStar property export of pipeline hotels", required: true, enabled: true },
  { id: "drivers", label: "Drivers", hint: "drivers.yaml from /forecast, or your own cards", required: false, enabled: true, accept: ".yaml,.yml,.json" },
];

async function onFile(slot: string, file: File | undefined) {
  if (!file) return;
  state.files.set(slot, file);
  if (slot === "pipeline" && state.info) {
    try {
      const geo = await engine.peek(file);
      const m = geo.market?.[0], s = geo.submarket?.[0];
      const known = state.info.markets.find((k) => k.costar_market === m && (k.costar_submarket ?? undefined) === (s ?? undefined));
      if (known) state.market = known.slug;
      else if (m) { state.market = "__other"; state.other = { ...state.other, name: s ? `${m}, ${s}` : m, market: m, submarket: s ?? "" }; }
    } catch { /* the run reports file problems */ }
  }
  renderInputs();
}

function slotView(s: Slot) {
  const f = state.files.get(s.id);
  const inputId = `file-${s.id}`;
  const input = h("input", { type: "file", id: inputId, accept: s.accept ?? ".xlsx,.xlsm,.csv",
    onchange: (e: Event) => onFile(s.id, (e.target as HTMLInputElement).files?.[0]) });
  const box = h("div", { class: `slot${f ? " has-file" : ""}`, "data-slot": s.id },
    h("div", { class: "row" }, h("span", { class: "label" }, s.label), h("span", { class: "req" }, s.required ? "required" : "optional")),
    h("div", { class: "hint" }, s.hint),
    f ? h("div", { class: "file" }, h("span", { class: "name" }, f.name),
          h("button", { class: "btn link", type: "button", onclick: () => { state.files.delete(s.id); renderInputs(); } }, "Remove"))
      : h("div", { class: "pick" }, h("label", { for: inputId, class: "btn", tabindex: "0",
          onkeydown: (e: KeyboardEvent) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); } } }, "Choose file"),
          h("span", { class: "small muted" }, " or drop it here")),
    input);
  box.addEventListener("dragover", (e) => { e.preventDefault(); box.classList.add("drag"); });
  box.addEventListener("dragleave", () => box.classList.remove("drag"));
  box.addEventListener("drop", (e) => { e.preventDefault(); box.classList.remove("drag"); onFile(s.id, e.dataTransfer?.files?.[0]); });
  return box;
}

function marketChoice(): MarketChoice {
  if (state.market && state.market !== "__other") return { slug: state.market };
  const o = state.other;
  return { adhoc: { name: o.name || "Unnamed market", currency: o.currency || "EUR", costar_market: o.market || undefined, costar_submarket: o.submarket || undefined } };
}

function renderInputs() {
  const slots = (state.info?.slots ?? DEFAULT_SLOTS).filter((s) => s.enabled);
  const ready = !!state.info;
  const missing = slots.filter((s) => s.required && !state.files.has(s.id)).map((s) => s.label.toLowerCase());
  const canRun = ready && !missing.length && !state.running && (state.market !== "__other" || !!state.other.name);
  const other = state.market === "__other";
  inputsEl.replaceChildren(
    h("p", { class: "privacy" }, "Files are processed in this browser. Nothing is uploaded or stored on a server, except pipeline addresses sent to OpenStreetMap when you click Find locations on the Map tab."),
    h("div", {}, slots.map(slotView)),
    h("div", { class: "field" },
      h("label", { for: "market" }, "Market"),
      h("select", { id: "market", disabled: !ready, onchange: (e: Event) => { state.market = (e.target as HTMLSelectElement).value; renderInputs(); } },
        (state.info?.markets ?? []).map((m) => h("option", { value: m.slug, selected: state.market === m.slug }, `${m.name} (${m.currency})`)),
        h("option", { value: "__other", selected: other }, "Other market")),
      other ? h("div", { class: "row2" },
        h("input", { type: "text", "aria-label": "Market name", placeholder: "Market name", value: state.other.name,
          oninput: (e: Event) => { state.other.name = (e.target as HTMLInputElement).value; }, onchange: () => renderInputs() }),
        h("input", { type: "text", "aria-label": "Currency code", placeholder: "EUR", maxlength: "3", value: state.other.currency,
          oninput: (e: Event) => { state.other.currency = (e.target as HTMLInputElement).value.toUpperCase(); } })) : null),
    h("div", { class: "run-row" },
      h("button", { class: "btn primary", type: "button", disabled: !canRun, onclick: run }, state.running ? "Running…" : "Run"),
      h("span", { class: "msg" }, !ready ? "Waiting for the engine" : missing.length ? `Add ${missing.join(" and ")}` : "")),
  );
}

async function run() {
  state.running = true;
  state.error = "";
  renderInputs();
  try {
    const files = [...state.files.entries()].map(([slot, file]) => ({ slot, file }));
    loadSaved();
    state.result = (await engine.run(files, marketChoice(), state.settings, editedCards(state.customDrivers))) as Result;
    const customIds = new Set(state.customDrivers.map((c) => c.id));
    state.fileCards = (state.result.forecast?.drivers?.cards ?? []).filter((c: any) => !customIds.has(c.id));
    state.tab = state.result.forecast ? "forecast" : "data";
  } catch (e) {
    state.error = e instanceof Error ? e.message : String(e);
    state.result = null;
  } finally {
    state.running = false;
    renderInputs();
    renderResults();
  }
}

// ---------- settings persistence ----------
const storeKey = () => `hmf-ledger:${state.result?.meta.slug ?? state.market}`;
function loadSaved() {
  try {
    const raw = localStorage.getItem(`hmf-ledger:${state.market === "__other" ? (state.other.name || "market").toLowerCase().replace(/[^a-z0-9]+/g, "-") : state.market}`);
    if (raw) { const v = JSON.parse(raw); state.settings = v.settings ?? {}; state.customDrivers = v.customDrivers ?? []; state.driverEdits = v.driverEdits ?? {}; }
  } catch { /* storage unavailable */ }
}
function save() {
  try { localStorage.setItem(storeKey(), JSON.stringify({ settings: state.settings, customDrivers: state.customDrivers, driverEdits: state.driverEdits })); }
  catch { /* storage unavailable */ }
}
function changedCount(): number {
  const defaults = new Map((state.result?.settings_schema ?? []).flatMap((g) => g.fields.map((f) => [f.key, f.default] as const)));
  const n = Object.entries(state.settings).filter(([k, v]) => defaults.has(k) && v !== defaults.get(k)).length;
  const d = Object.values(state.driverEdits).filter((e) => e.enabled === false || e.probability !== undefined).length;
  return n + d + state.customDrivers.length;
}
function editedCards(cards: any[]): any[] {
  return cards.filter((c) => state.driverEdits[c.id]?.enabled !== false).map((c) => {
    const e = state.driverEdits[c.id];
    return e?.probability !== undefined ? { ...c, probability: e.probability, probability_note: c.probability_note || "Set by the user on the Assumptions tab." } : c;
  });
}
async function rerun() {
  if (!state.result) return;
  state.rerunning = true;
  renderResults();
  try {
    const out = (await engine.rerun(state.settings, editedCards([...state.fileCards, ...state.customDrivers]), false)) as Partial<Result>;
    state.result = { ...state.result, forecast: out.forecast, forecast_error: out.forecast_error };
  } catch (e) {
    state.result = { ...state.result, forecast_error: e instanceof Error ? e.message : String(e) };
  } finally {
    state.rerunning = false;
    renderResults();
  }
}

// ---------- shared view helpers ----------
const TABS: [string, string][] = [["forecast", "Forecast"], ["supply", "Supply"], ["map", "Map"], ["assumptions", "Assumptions"], ["data", "Data"]];
const BASIS: Record<string, [string, string]> = {
  fitted: ["pass", "fitted"], assumption: ["warn", "assumption"], user_decision: ["info", "your decision"], fact: ["info", "fact"],
  default: ["info", "default"], "driver (approved)": ["pass", "driver, approved"], "driver (proposed)": ["warn", "driver, proposed"],
};
function badge(cls: string, text: string) { return h("span", { class: `badge ${cls}` }, text); }
function basisBadge(b: string | undefined) { const [c, t] = BASIS[b ?? ""] ?? ["info", b ?? ""]; return t ? badge(c, t) : ""; }
const pp = (v: number) => `${v >= 0 ? "+" : ""}${(v * 100).toFixed(2)}`;

function table(head: (string | [string, string])[], rows: (Node | string)[][], rowClass?: (i: number) => string) {
  return h("div", { class: "table-wrap" }, h("table", {},
    h("thead", {}, h("tr", {}, head.map((c) => Array.isArray(c) ? h("th", { class: c[1], scope: "col" }, c[0]) : h("th", { scope: "col" }, c)))),
    h("tbody", {}, rows.map((r, i) => h("tr", { class: rowClass?.(i) ?? "" }, r.map((c, j) => {
      const hc = head[j];
      return h("td", { class: Array.isArray(hc) ? hc[1] : "" }, c);
    }))))));
}

function renderEmpty() {
  resultsEl.replaceChildren(h("div", { class: "empty" },
    h("h2", {}, "How it works"),
    h("ol", {},
      h("li", {}, "Drop the CoStar monthly performance export and the pipeline export. Optionally drop the drivers.yaml from /forecast."),
      h("li", {}, "Run. Each year's forecast is built as a ledger: the market's long-run RevPAR CAGR, then named adjustments for supply above trend, the quality of new and closing hotels, and drivers such as events, air access and risk."),
      h("li", {}, "Every line says why it is there and what it rests on. Change any assumption on the Assumptions tab and rerun in a second.")),
    state.error ? h("div", { class: "error-box" }, `The run failed: ${state.error}`) : null));
}

function renderResults() {
  disposeCharts();
  const r = state.result;
  if (!r) { renderEmpty(); return; }
  const f = r.forecast;
  const nWarn = Object.values(r.checks).flat().filter((c) => c.status === "warn").length;
  const tabs = h("nav", { class: "tabs", role: "tablist", "aria-label": "Result views" },
    TABS.map(([id, label]) => {
      const enabled = id === "data" || (id === "map" ? !!r.pipeline_map : !!f);
      return h("button", { role: "tab", type: "button", id: `tab-${id}`, "aria-selected": String(state.tab === id), "aria-controls": "panel",
        disabled: !enabled, onclick: () => { state.tab = id; renderResults(); } },
        id === "assumptions" && changedCount() ? `${label} (${changedCount()} changed)` : label);
    }));
  const panel = h("div", { class: "panel", id: "panel", role: "tabpanel", "aria-labelledby": `tab-${state.tab}` });
  resultsEl.replaceChildren(
    h("div", { class: "summary" },
      h("span", { class: "title" }, r.meta.market),
      r.meta.passed ? badge("pass", "Data passed validation") : badge("fail", "Data failed validation"),
      nWarn ? badge("warn", `${nWarn} data warning${nWarn > 1 ? "s" : ""}`) : null,
      f?.meta.provisional ? badge("warn", "Provisional: proposed drivers included") : null,
      f?.adjustments.length ? badge("info", `${f.adjustments.length} adjustment${f.adjustments.length > 1 ? "s" : ""}`) : null),
    r.fatal.length ? h("div", { class: "error-box" }, "The run stopped:", h("ul", {}, r.fatal.map((x) => h("li", {}, x)))) : "",
    r.forecast_error ? h("div", { class: "error-box" }, `The forecast could not run: ${r.forecast_error}`) : "",
    r.drivers_error ? h("div", { class: "error-box" }, `The drivers file could not be read: ${r.drivers_error}`) : "",
    f ? h("div", { class: "facts" },
      h("div", {}, h("span", {}, "Data to"), f.meta.last_actual_month),
      h("div", {}, h("span", {}, "Baseline"), `${f.baseline.start} to ${f.baseline.end}, RevPAR ${signedPct(f.baseline.revpar)} a year`),
      h("div", {}, h("span", {}, "Calibration"), f.meta.calibrated_on ? `${f.meta.calibrated_on} (European panel ${f.meta.panel_snapshot})` : "not calibrated")) : "",
    tabs, panel,
    h("footer", { class: "foot" }, `Engine ${r.meta.engine_version}, Pyodide ${PYODIDE_VERSION}. Computed in this browser on ${r.meta.date}.`));
  ({ forecast: forecastView, supply: supplyView, map: mapView, assumptions: assumptionsView, data: dataView } as Record<string, (p: HTMLElement, r: Result) => void>)[state.tab]?.(panel, r);
}

// ---------- Forecast ----------
function forecastView(p: HTMLElement, r: Result) {
  const f = r.forecast!;
  const cur = f.meta.currency;
  const f5 = f.five_year;
  p.append(
    table(["Year", ["Occupancy", "n"], ["ADR", "n"], ["RevPAR", "n"], ["RevPAR P10 to P90", "n"], ["Occupancy level", "n"], [`ADR (${cur})`, "n"]],
      f.years.map((y) => [y.label, signedPct(y.occ_growth), signedPct(y.adr_growth), h("b", {}, signedPct(y.revpar_growth)),
        `${signedPct(y.range.revpar_growth.p10)} to ${signedPct(y.range.revpar_growth.p90)}`, pct(y.occupancy), num(y.adr, 1)])),
    h("p", {}, `Five-year RevPAR CAGR ${f5.from} to ${f5.to}: `, h("b", {}, signedPct(f5.revpar_cagr)),
      ` (P10 ${signedPct(f5.p10)}, P90 ${signedPct(f5.p90)}), against the long-run ${signedPct(f5.baseline_revpar_cagr)}. `,
      "RevPAR growth = occupancy growth combined with ADR growth."));
  const chartEl = h("div", { class: "chart", role: "img", "aria-label": "History and forecast" });
  const metrics: [typeof state.metric, string][] = [["revpar", "RevPAR"], ["occupancy", "Occupancy"], ["adr", "ADR"]];
  p.append(h("div", { class: "downloads", role: "group", "aria-label": "Chart measure" },
    metrics.map(([k, lab]) => h("button", { class: `btn${state.metric === k ? " primary" : ""}`, type: "button", "aria-pressed": String(state.metric === k),
      onclick: () => { state.metric = k; renderResults(); } }, lab))), chartEl);
  requestAnimationFrame(() => {
    const k = state.metric;
    const money = (v: number) => num(v, 0);
    ledgerChart(chartEl, {
      history: f.history.filter((x) => x.complete).map((x) => ({ year: x.year, value: x[k] })),
      forecast: f.years.map((y) => ({ year: y.year, label: y.label, value: y[k], band: y.range[k] })),
      fmt: k === "occupancy" ? (v) => `${(v * 100).toFixed(1)}%` : money, unit: k === "occupancy" ? "" : cur,
    });
  });
  p.append(h("h2", {}, "Why each year sits where it does"),
    h("p", { class: "note" }, "Each year is the sum of its lines, in points of growth. Basis: fitted on European submarket data, a labelled assumption, your decision, a fact, or a driver weighted by its chance."));
  for (const y of f.years) {
    p.append(h("div", { class: "year-story" },
      h("h3", { class: "year-head" }, `${y.label}: RevPAR ${signedPct(y.revpar_growth)}`,
        h("span", { class: "muted small" }, `  occupancy ${signedPct(y.occ_growth)}, ADR ${signedPct(y.adr_growth)}; ${Math.round(y.range.p_revpar_fall * 100)}% chance RevPAR falls`)),
      table(["Layer", "Line", ["Occupancy", "n"], ["ADR", "n"], "Basis"],
        [...y.lines.map((l) => [l.layer, l.title, pp(l.occ), pp(l.adr), basisBadge(l.basis)]),
          ["", h("b", {}, "Total"), h("b", {}, pp(y.occ_growth)), h("b", {}, pp(y.adr_growth)), ""]]),
      h("details", {}, h("summary", {}, "Reasons"),
        h("ul", { class: "details" }, y.lines.map((l) => h("li", {}, h("b", {}, `${l.title}. `), l.text,
          l.layer === "Drivers" && l.evidence ? h("div", { class: "small muted" }, `Working: ${l.evidence}`) : "")))),
    ));
  }
  if (f.adjustments.length) p.append(h("h2", {}, "Your adjustments"), h("ul", {}, f.adjustments.map((a) => h("li", {}, a))));
  if (f.gaps.length) p.append(h("h2", {}, "Gaps"), h("ul", {}, f.gaps.map((g) => h("li", {}, g))));
  const ev = f.calibration.evidence?.cagr_rule_backtest;
  p.append(h("h2", {}, "Ranges"), h("p", { class: "note" }, f.ranges.note, " ", f.ranges.source ?? "",
    ` Volatility relative to the European norm: occupancy x${f.ranges.volatility_ratio.occ.toFixed(2)}, ADR x${f.ranges.volatility_ratio.adr.toFixed(2)}.`,
    ev ? ` Track record of the CAGR rule: it missed five-year RevPAR CAGR by ${(ev.five_year_revpar_cagr_mae * 100).toFixed(1)} points a year on average across ${ev.n} European five-year periods; the supply adjustment cut that to ${(ev.mae_with_supply_adjustment * 100).toFixed(1)}.` : ""));
  const csv = ["year,layer,line,occupancy_points,adr_points,basis", ...f.years.flatMap((y) => y.lines.map((l) =>
    [y.year, l.layer, `"${l.title.replace(/"/g, "'")}"`, (l.occ * 100).toFixed(3), (l.adr * 100).toFixed(3), l.basis].join(",")))].join("\n");
  p.append(h("div", { class: "downloads" },
    h("button", { class: "btn", type: "button", onclick: () => download(`forecast-${r.meta.slug}.md`, f.report_md, "text/markdown") }, "Download forecast.md"),
    h("button", { class: "btn", type: "button", onclick: () => download(`ledger-${r.meta.slug}.csv`, csv, "text/csv") }, "Download ledger.csv"),
    h("button", { class: "btn", type: "button", onclick: () => download(`results-${r.meta.slug}.json`, JSON.stringify(r, null, 1), "application/json") }, "Download results.json")));
}

// ---------- Supply ----------
function supplyView(p: HTMLElement, r: Result) {
  const s = r.forecast!.supply;
  p.append(
    h("p", {}, `Hotel inventory has grown ${signedPct(s.inventory_trend)} a year over the baseline window and never grows below that trend in the forecast. `,
      `The market is priced like ${s.average_tier} (assumption, set per market); new rooms above or below that tier move the average rate through mix, and compete less directly the further they sit from it.`),
    table(["Year", ["Available supply", "n"], ["Inventory", "n"], ["Against trend", "n"], ["New builds", "n"], ["Reopenings", "n"], ["Unannounced", "n"], ["Removed", "n"], ["Rate mix index", "n"], ["Overlap", "n"]],
      s.years.map((y) => [String(y.year), signedPct(y.supply_growth), signedPct(y.inventory_growth), `${(y.excess_vs_trend * 100).toFixed(1)} pts`,
        num(y.new_builds_rooms), num(y.reopening_rooms), num(y.unannounced_rooms), num(y.removed_rooms), y.adr_mix_index.toFixed(3), pct(y.overlap, 0)])),
    h("p", { class: "note" }, "Rooms are average rooms in the year, expected values across the simulated pipeline outcomes."),
    h("h2", {}, "Pipeline"),
    exportButton(r),
    table(["Project", ["Rooms", "n"], "Stage", "Tier", "Product", ["Chance of opening", "n"], "Median opening"],
      s.projects.map((x) => [x.name, num(x.rooms), x.stage.replace(/_/g, " "), x.tier, x.product ?? "hotel", pct(x.p_open_horizon, 0), x.median_open ?? ""])),
    s.notes.length ? h("ul", { class: "details" }, s.notes.map((n) => h("li", {}, n))) : "");
}

// ---------- Map and pipeline export ----------
const mapKey = (sites: Site[]) => sites.map((x) => `${x.n}:${x.lat ?? ""},${x.lon ?? ""}`).join("|");

async function ensureMap(sites: Site[]) {
  const key = mapKey(sites);
  if (state.map?.key === key) return state.map;
  const { canvas, tiles } = await renderMap(sites);
  state.map = { key, canvas, tiles };
  return state.map;
}

function exportButton(r: Result) {
  if (!r.pipeline_map) return "";
  return h("div", { class: "downloads" }, h("button", { class: "btn", type: "button", disabled: state.exporting, onclick: () => exportPipeline(r) },
    state.exporting ? "Preparing…" : "Export pipeline (Excel)"));
}

async function exportPipeline(r: Result) {
  const pm = r.pipeline_map!;
  state.exporting = true;
  renderResults();
  try {
    const located = pm.sites.filter((x) => x.lat != null && x.lon != null);
    let png: ArrayBuffer | null = null;
    if (located.length) {
      const m = await ensureMap(pm.sites);
      const blob: Blob | null = await new Promise((ok) => m.canvas.toBlob(ok, "image/png"));
      png = blob ? await blob.arrayBuffer() : null;
    }
    const bytes = await engine.exportPipeline(png);
    const url = URL.createObjectURL(new Blob([bytes], { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" }));
    const a = h("a", { href: url, download: `pipeline-${r.meta.slug}.xlsx` });
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (e) {
    alert(`The export failed: ${e instanceof Error ? e.message : String(e)}`);
  } finally {
    state.exporting = false;
    renderResults();
  }
}

function mapView(p: HTMLElement, r: Result) {
  const pm = r.pipeline_map!;
  const sites = pm.sites;
  applyCached(sites);
  const located = sites.filter((x) => x.lat != null && x.lon != null).length;
  const missing = sites.length - located;
  p.append(
    h("p", {}, `${sites.length} pipeline project${sites.length === 1 ? "" : "s"}, numbered by CoStar's projected opening date. `,
      `${located} located on the map${missing ? `, ${missing} not yet` : ""}.`),
    h("p", { class: "note" }, "Locations come from the export's latitude and longitude when it has them. Otherwise, Find locations sends each project's address to OpenStreetMap's geocoder (one request a second) and keeps the results in this browser. The base map loads from OpenStreetMap and CARTO; no project data goes with it."),
    h("div", { class: "downloads" },
      missing ? h("button", { class: "btn primary", type: "button", disabled: !!state.geocoding, onclick: async () => {
        state.geocoding = `0 of ${missing}`;
        renderResults();
        await geocode(sites, (d, t) => { state.geocoding = `${d} of ${t}`; const el = document.getElementById("geo-progress"); if (el) el.textContent = `Finding locations: ${d} of ${t}`; });
        state.geocoding = "";
        state.map = null;
        renderResults();
      } }, state.geocoding ? "Finding locations…" : `Find locations (${missing})`) : "",
      h("span", { id: "geo-progress", class: "msg small muted" }, state.geocoding ? `Finding locations: ${state.geocoding}` : ""),
      exportButton(r)));
  const holder = h("div", { class: "map-holder" }, located ? h("p", { class: "small muted" }, "Drawing the map…") : h("p", { class: "small muted" }, "No locations yet."));
  p.append(holder,
    h("div", { class: "legend small" }, Object.entries(STAGE_COLOR).filter(([k]) => sites.some((x) => x.stage === k)).map(([k, c]) =>
      h("span", {}, h("span", { class: "swatch", style: `background:${c}` }), sites.find((x) => x.stage === k)!.stage_label))));
  if (located) {
    void ensureMap(sites).then((m) => {
      const img = h("img", { src: m.canvas.toDataURL("image/png"), alt: "Map of pipeline projects", class: "pipeline-map" });
      holder.replaceChildren(img, m.tiles ? "" : h("p", { class: "small muted" }, "The base map could not be loaded (no connection to the tile server); pins are placed on a blank background."));
    });
  }
  const years = [...new Set(sites.map((x) => x.year).filter((y): y is number => y != null))].sort();
  let total = pm.current_supply ?? 0;
  p.append(
    table(["#", "Name", ["Keys", "n"], "Opening", "Class", "Stage", "Owner", "Operator", "Location"], sites.map((x) => [
      h("span", { class: "pin", style: `background:${STAGE_COLOR[x.stage] ?? "#7b1e45"}` }, String(x.n)), x.name, num(x.rooms),
      x.opening ? new Date(x.opening).toLocaleDateString("en-GB", { month: "short", year: "numeric" }) : "", x.class ?? "", x.stage_label,
      x.owner ?? "", x.operator ?? "", x.lat != null ? `${x.lat.toFixed(4)}, ${x.lon!.toFixed(4)}` : h("span", { class: "muted" }, [x.address, x.city, x.postcode].filter(Boolean).join(", "))])),
    pm.current_supply ? h("div", {}, h("h2", {}, "Pipeline supply by year"),
      table(["Year", ["Incoming", "n"], ["Total", "n"], ["%", "n"]], [
        ["Current supply", "", num(pm.current_supply), `as of ${pm.as_of}`],
        ...years.map((y) => {
          const inc = sites.filter((x) => x.year === y).reduce((a, b) => a + b.rooms, 0);
          const prev = total;
          total += inc;
          return [String(y), num(inc), num(total), `${((total / prev - 1) * 100).toFixed(1)}%`];
        })]),
      h("p", { class: "note" }, "Every project counted in full in its CoStar opening year, as in the Excel export. The forecast itself weights projects by their chance of opening and typical delays (Supply tab).")) : "");
}

// ---------- Assumptions ----------
function fmtSetting(kind: string, v: unknown, fd?: SettingField): string {
  if (v === null || v === undefined) return "auto";
  if (kind === "select") return (fd?.options ?? []).find(([k]) => k === v)?.[1] ?? String(v);
  if (kind === "year") return String(v);
  if (typeof v === "boolean") return v ? "on" : "off";
  const n = Number(v);
  if (kind === "percent") return `${(n * 100).toFixed(1)}%`;
  if (kind === "points") return `${n >= 0 ? "+" : ""}${(n * 100).toFixed(1)} pts`;
  if (kind === "months") return `${n} months`;
  return String(Math.round(n * 1000) / 1000);
}

function settingInput(fd: SettingField) {
  const scale = fd.kind === "percent" || fd.kind === "points" ? 100 : 1;
  const cur = fd.key in state.settings ? state.settings[fd.key] : fd.default;
  const id = `set-${fd.key.replace(/\./g, "-")}`;
  if (fd.kind === "select") {
    return h("select", { id, "aria-label": fd.label, onchange: (e: Event) => { state.settings[fd.key] = (e.target as HTMLSelectElement).value; save(); renderResults(); } },
      (fd.options ?? []).map(([v, lab]) => h("option", { value: v, selected: cur === v }, lab)));
  }
  if (fd.kind === "bool") {
    return h("input", { type: "checkbox", id, checked: Boolean(cur), "aria-label": fd.label,
      onchange: (e: Event) => { state.settings[fd.key] = (e.target as HTMLInputElement).checked; save(); renderResults(); } });
  }
  return h("input", { type: "number", id, class: "num-in", "aria-label": fd.label,
    step: String((fd.step ?? 0.01) * scale), min: fd.min != null ? String(fd.min * scale) : undefined, max: fd.max != null ? String(fd.max * scale) : undefined,
    value: cur === null || cur === undefined ? "" : String(Math.round(Number(cur) * scale * 1000) / 1000), placeholder: "auto",
    onchange: (e: Event) => {
      const raw = (e.target as HTMLInputElement).value.trim();
      if (raw === "") { if (fd.default === null) delete state.settings[fd.key]; else state.settings[fd.key] = fd.default; }
      else state.settings[fd.key] = Number(raw) / scale;
      save(); renderResults();
    } });
}

function assumptionsView(p: HTMLElement, r: Result) {
  const f = r.forecast!;
  const n = changedCount();
  p.append(
    h("p", {}, "Every coefficient the forecast uses is listed here with its basis: fitted on the European panel, a labelled assumption, or your decision. Change any value and rerun; changes are reported on the forecast as your adjustments. Fitted values are refreshed when the panel is recalibrated."),
    h("div", { class: "downloads sticky-actions" },
      h("button", { class: "btn primary", type: "button", disabled: state.rerunning, onclick: rerun }, state.rerunning ? "Rerunning…" : "Rerun forecast"),
      h("button", { class: "btn", type: "button", disabled: !n, onclick: () => { state.settings = {}; state.driverEdits = {}; state.customDrivers = []; save(); renderResults(); } }, "Reset to defaults"),
      h("button", { class: "btn", type: "button", onclick: () => download(`settings-${r.meta.slug}.json`, JSON.stringify({ settings: state.settings, driverEdits: state.driverEdits }, null, 2), "application/json") }, "Download settings"),
      h("button", { class: "btn", type: "button", onclick: () => download(`drivers-${r.meta.slug}.json`, JSON.stringify({ drivers: editedCards([...state.fileCards, ...state.customDrivers]) }, null, 2), "application/json") }, "Download drivers"),
      h("span", { class: "msg small muted" }, n ? `${n} change${n > 1 ? "s" : ""} from the defaults. Rerun to apply.` : "All values at their defaults.")));
  driversEditor(p, f, r);
  for (const g of r.settings_schema ?? []) {
    p.append(h("details", { class: "group", open: g.id === "baseline" || g.id === "supply" },
      h("summary", {}, h("b", {}, g.title), h("span", { class: "muted small" }, `  ${g.help}`)),
      table(["Setting", ["Value", "n"], ["Default", "n"], "Basis", "Evidence"], g.fields.map((fd) => {
        const changed = fd.key in state.settings && state.settings[fd.key] !== fd.default;
        return [h("label", { for: `set-${fd.key.replace(/\./g, "-")}` }, fd.label), settingInput(fd), fmtSetting(fd.kind, fd.default, fd),
          changed ? badge("warn", "changed") : basisBadge(fd.status), fd.evidence ? h("span", { class: "small muted" }, fd.evidence) : ""];
      }))));
  }
}

const COVERAGE_TEXT: Record<string, [string, string]> = {
  card: ["pass", "driver card"], no_material_effect: ["info", "checked, nothing material"], not_applicable: ["info", "not applicable"],
  could_not_verify: ["warn", "could not verify"], missing: ["fail", "not researched"],
};

function researchView(p: HTMLElement, f: Forecast, r: Result) {
  p.append(h("h2", {}, "Research"),
    h("p", { class: "note" }, "Run the driver research in Cowork (or with /forecast): download the brief for this market, give it to Cowork, then drop the file it saves into the Drivers slot and run again. The brief holds the 45-item checklist, the method, the card format and the calculators."),
    h("div", { class: "downloads" },
      r.research_brief ? h("button", { class: "btn", type: "button", onclick: () => download(`research-brief-${r.meta.slug}.md`, r.research_brief!, "text/markdown") }, "Download research brief") : ""));
  const cov = f.coverage;
  if (!cov) {
    p.append(h("p", { class: "small muted" }, state.fileCards.length ? "The drivers file has no coverage section, so research completeness cannot be checked." : "No research loaded yet."));
    return;
  }
  const errs = cov.issues.filter((i) => i.level === "error");
  p.append(h("p", {}, `${cov.answered} of ${cov.items} checklist items answered. `,
      errs.length ? badge("fail", `${errs.length} problem${errs.length > 1 ? "s" : ""}`) : badge("pass", "complete")),
    h("details", { class: "group", open: errs.length > 0 }, h("summary", {}, h("b", {}, "Research coverage")),
      table(["Item", "Area", "Answer", "Cards", "Finding"], cov.rows.map((x) => {
        const [c, t] = COVERAGE_TEXT[x.status] ?? ["info", x.status];
        const src = (x.sources ?? []).slice(0, 3).map((s) => s.url ? h("a", { href: s.url, target: "_blank", rel: "noopener" }, s.publisher ?? "source") : "");
        return [x.title, x.owner, badge(c, t), x.cards.join(", "), h("span", { class: "small" }, x.finding ?? "", src.length ? " Sources: " : "", ...src.flatMap((a, i) => i ? [", ", a] : [a]))];
      })),
      errs.length ? h("ul", { class: "details" }, errs.map((i) => h("li", {}, i.text))) : ""));
}

function driversEditor(p: HTMLElement, f: Forecast, r: Result) {
  researchView(p, f, r);
  const issues = f.drivers.issues ?? [];
  const ladder = f.drivers.ladder ?? {};
  const applied = new Map(f.drivers.applied.map((a) => [a.id, a]));
  const all = [...state.fileCards.map((c) => ({ c, src: c.origin === "user" ? "file (yours)" : "research" })), ...state.customDrivers.map((c) => ({ c, src: "added here" }))];
  p.append(h("h2", {}, "Drivers (layer 3)"),
    h("p", { class: "note" }, "Switch a driver off or change its chance. Event chances follow the status ladder unless set: ",
      Object.entries(ladder).map(([k, v]) => `${k.replace(/_/g, " ")} ${Math.round(v.default * 100)}%`).join(", "), "."));
  if (!all.length) p.append(h("p", {}, "No drivers loaded. Drop a drivers.yaml on the left, or add one below."));
  else p.append(table(["Include", "Driver", "Affects", "From", ["Chance (%)", "n"], "Size", "Checks", ""],
    all.map(({ c, src }) => {
      const e = state.driverEdits[c.id] ?? {};
      const pr = e.probability ?? c.probability ?? (c.event_status ? ladder[c.event_status]?.default : null);
      const a = applied.get(c.id);
      const unit = c.magnitude?.unit === "rooms" ? " rooms" : c.magnitude?.unit === "multiplier" ? "x" : "%";
      const size = c.calculator ? h("details", {}, h("summary", {}, `calculator: ${c.calculator.type}`), h("ul", { class: "details" }, (a?.working ?? []).map((w) => h("li", {}, w))))
        : c.magnitude ? `${c.magnitude.low} to ${c.magnitude.high}${unit} (most likely ${c.magnitude.mode}${unit})` : "";
      const iss = issues.filter((i) => i.id === c.id && i.level !== "info");
      return [
        h("input", { type: "checkbox", checked: e.enabled !== false, "aria-label": `Include ${c.title}`,
          onchange: (ev: Event) => { state.driverEdits[c.id] = { ...e, enabled: (ev.target as HTMLInputElement).checked }; save(); renderResults(); } }),
        h("span", {}, c.title, h("div", { class: "small muted" }, src)), String(c.affects).replace(/_/g, " "), String(c.timing.start_year),
        h("input", { type: "number", class: "num-in", min: "0", max: "100", step: "5", "aria-label": `Chance for ${c.title}`,
          value: pr === null || pr === undefined ? "" : String(Math.round(pr * 1000) / 10),
          onchange: (ev: Event) => { const v = (ev.target as HTMLInputElement).value.trim(); state.driverEdits[c.id] = { ...e, probability: v === "" ? undefined : Number(v) / 100 }; save(); renderResults(); } }),
        size,
        iss.length ? h("details", {}, h("summary", {}, `${iss.length}`), h("ul", { class: "details" }, iss.map((i) => h("li", {}, `${i.level}: ${i.text}`)))) : "ok",
        src === "added here" ? h("button", { class: "btn link", type: "button", onclick: () => { state.customDrivers = state.customDrivers.filter((x) => x.id !== c.id); save(); renderResults(); } }, "Remove") : "",
      ];
    })));
  const years = f.years.map((y) => y.year);
  const form = h("form", { class: "driver-form", onsubmit: (ev: Event) => {
    ev.preventDefault();
    const fd = new FormData(ev.target as HTMLFormElement);
    const g = (k: string) => String(fd.get(k) ?? "").trim();
    const affects = g("affects");
    const unit = affects === "supply" ? "rooms" : affects === "shock_probability" ? "multiplier" : "pct_of_level";
    const year = Number(g("year"));
    const card: any = {
      id: `user-${Date.now().toString(36)}`, title: g("title") || "Untitled driver", category: affects === "shock_probability" ? "geopolitical" : "events",
      affects, timing: { start_year: year, peak_year: year, decay_years: Number(g("decay") || 0) }, shape: g("shape"),
      magnitude: { low: Number(g("low")), mode: Number(g("mode")), high: Number(g("high")), unit },
      already_in_baseline: "none", baseline_overlap_note: g("overlap") || "Added by the user; judged not to be in the historical trend.",
      origin: "user", status: "approved", confidence: "low",
    };
    if (g("prob") !== "") card.probability = Number(g("prob")) / 100;
    if (g("status")) card.event_status = g("status");
    if (!(card.magnitude.low <= card.magnitude.mode && card.magnitude.mode <= card.magnitude.high)) { alert("Size must satisfy low <= most likely <= high."); return; }
    state.customDrivers.push(card);
    save();
    renderResults();
  } },
    h("h3", {}, "Add an event or risk"),
    h("div", { class: "form-grid" },
      h("label", {}, "Title", h("input", { name: "title", required: true, placeholder: "e.g. Stadium concert series" })),
      h("label", {}, "Affects", h("select", { name: "affects" },
        h("option", { value: "demand" }, "Demand (percent of the year's demand)"), h("option", { value: "adr" }, "ADR (percent of the year's ADR)"),
        h("option", { value: "shock_probability" }, "Chance of a downturn (multiplier)"), h("option", { value: "supply" }, "Supply (rooms)"))),
      h("label", {}, "Year", h("input", { name: "year", type: "number", required: true, value: String(years[1] ?? years[0]), min: String(years[0]), max: String(years[years.length - 1]) })),
      h("label", {}, "Shape", h("select", { name: "shape" },
        h("option", { value: "one_off" }, "One-off (that year only)"), h("option", { value: "temporary" }, "Temporary (fades)"), h("option", { value: "step" }, "Permanent"))),
      h("label", {}, "Decay years", h("input", { name: "decay", type: "number", min: "0", max: "5", value: "0" })),
      h("label", {}, "Event status", h("select", { name: "status" }, h("option", { value: "" }, "not set"),
        Object.keys(ladder).map((k) => h("option", { value: k }, `${k.replace(/_/g, " ")} (${Math.round(ladder[k].default * 100)}%)`)))),
      h("label", {}, "Chance (%), blank to use the status", h("input", { name: "prob", type: "number", min: "0", max: "100", step: "5" })),
      h("label", {}, "Size: low", h("input", { name: "low", type: "number", step: "any", required: true, value: "0.5" })),
      h("label", {}, "Size: most likely", h("input", { name: "mode", type: "number", step: "any", required: true, value: "1" })),
      h("label", {}, "Size: high", h("input", { name: "high", type: "number", step: "any", required: true, value: "2" })),
      h("label", { class: "wide" }, "Why this is not already in the historical trend", h("input", { name: "overlap" }))),
    h("p", { class: "note" }, "Sizes are percent of the year's level for demand and ADR (negative for a fall), rooms for supply, and a multiplier for the chance of a downturn. For sized calculators (attendance, seats, source-market shares), use /forecast or a drivers file."),
    h("button", { class: "btn", type: "submit" }, "Add driver"));
  p.append(form);
}

// ---------- Data ----------
function dataView(p: HTMLElement, r: Result) {
  const all = Object.entries(r.checks).flatMap(([g, cs]) => cs.map((c) => ({ g, c })));
  const flagged = all.filter(({ c }) => c.status === "warn" || c.status === "fail");
  p.append(
    h("p", {}, `${all.length} checks run; ${all.filter(({ c }) => c.status === "pass").length} passed, ${flagged.length} flagged.`),
    flagged.length ? table(["File", "Check", "Status", "Message"], flagged.map(({ g, c }) => [g, `${c.id} ${c.title}`, badge(c.status, c.status), c.message])) : "",
    h("h2", {}, "Files read"),
    table(["File", "Read as", ["Rows", "n"], "Snapshot"], r.files.map((x) => [x.filename, x.mapping ?? x.note, num(x.rows), x.snapshot_date ?? ""])),
    r.breaks.length ? h("div", {}, h("h2", {}, "Series breaks found"), h("ul", {}, r.breaks.map((b) => h("li", {}, b.description)))) : "",
    h("div", { class: "downloads" },
      h("button", { class: "btn", type: "button", onclick: () => download(`validation-${r.meta.slug}.md`, r.validation_md, "text/markdown") }, "Download validation.md")));
}
