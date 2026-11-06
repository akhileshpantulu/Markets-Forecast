import "./styles.css";
import { Engine, PYODIDE_VERSION, type AppInfo, type MarketChoice, type Slot } from "./engine";
import { annualChart, disposeCharts, fanChart, monthlyChart, stubChart, type Quantiles } from "./charts";
import { download, h, num, pct, pts, signedPct } from "./format";

// ---------- result types (subset of engine/web_api.py output) ----------
interface Check { id: string; level: string; status: "pass" | "warn" | "fail" | "info"; title: string; message: string; details: string[] }
interface Result {
  meta: { run_id: string; engine_version: string; market: string; slug: string; currency: string; passed: boolean; date: string;
    first_month: string | null; last_month: string | null; stub_year: number | null; stub_actual_months: number | null };
  fatal: string[];
  files: { category: string; filename: string; mapping: string | null; sheet: string | null; rows: number; snapshot_date: string | null;
    mapped: [string, string][]; ignored: string[]; duplicate_headers: string[]; note: string }[];
  checks: Record<string, Check[]>;
  annual: { year: number; months_observed: number; complete: boolean; supply: number; demand: number; occupancy: number; adr: number; revpar: number }[];
  monthly: { period: string; occupancy: number; adr: number }[];
  pipeline: { project_id: string; name: string; rooms: number; stage: string; project_type: string; projected_open: string | null;
    projected_open_basis: string; chain_scale: string | null; brand: string | null; operation_status: string | null }[];
  breaks: { measure: string; start: string; step: number; affected_years: number[]; action: string; decided_by: string; description: string; note: string }[];
  growth_sample: Record<string, number | string | boolean | null>[];
  influence: Record<string, { flagged: string[]; years: number[] }>;
  validation_md: string;
  forecast?: Forecast;
  forecast_error?: string;
  drivers_error?: string;
  settings_schema?: SettingGroup[];
}

interface SettingField { key: string; label: string; kind: string; min?: number; max?: number; step?: number; default: number | boolean | null }
interface SettingGroup { id: string; title: string; help: string; fields: SettingField[] }

type Q = Quantiles;
interface SummaryRow { year: number; label: string; occ_growth: Q; occ_pts: Q; adr_growth: Q; revpar_growth: Q; demand_growth: Q;
  supply_growth: Q; occupancy: Q; adr: Q; revpar: Q; p_revpar_negative: number; p_shock_onset: number | null }
interface Forecast {
  meta: { engine_version: string; seed: number; paths: number; stub_year: number; base_year: number; last_actual_month: string;
    variant: string; spread: number; calibrated: boolean; adr_mode: string; status: string; seconds: number; currency: string };
  summary: SummaryRow[];
  positions: { year: number; occ_growth: { percentile: number; outside_p5_p95: boolean }; adr_growth: { percentile: number; outside_p5_p95: boolean };
    revpar_growth: { percentile: number; outside_p5_p95: boolean }; analog_year: number | null; occ_above_hist_max: boolean }[];
  covid_sensitivity: { default: string; by_year: { year: number; occ_growth: { difference: number }; adr_growth: { difference: number }; revpar_growth: { difference: number } }[] };
  stub: { actual: { period: string; occupancy: number; adr: number }[]; simulated: Record<string, number | string>[];
    calibration_years: number[]; trend_demand: number; trend_adr: number; err_demand_sd: number | null; err_adr_sd: number | null } | null;
  base: { year: number; occupancy: number; adr: number; revpar: number };
  models: { demand: Record<string, any>; adr: Record<string, any>; ceiling: { annual: number; basis: string } };
  supply: { base_available_rooms: number; reference_occupancy: number; projects: Record<string, any>[]; notes: string[] };
  history: { annual: Record<string, any>[]; growth_percentiles: Record<string, any>[]; cycles: Record<string, any>[];
    decomposition: Record<string, any>[]; seasonality: Record<string, any>[]; seasonality_shift: Record<string, any>;
    occ_annual_max: number; occ_annual_min: number; notes: string[] };
  backtest: { origins: number[]; chosen: string; spread: number; spread_by_h?: number[]; calibrated: boolean; coverage: Record<string, any>[];
    benchmarks: Record<string, any>[]; variants: Record<string, any>[]; notes: string[] };
  explanation?: { bridge: Record<string, any>[]; flags: { level: string; year: number | null; text: string }[]; drivers: string[];
    per_year: { year: number; text: string }[]; cumulative_revpar: Record<string, number>; note: string };
  adjustments?: string[];
  settings_effective?: Record<string, any>;
  drivers?: { cards: any[]; applied: any[]; issues: { id: string; level: string; text: string }[]; attribution: any[]; ladder: Record<string, any> };
  macro?: { adr_mode: string; cpi_path: Record<string, number | null> | null; cpi_source: { publisher: string; title: string; url: string; accessed: string } | null; cpi_projection_note: string | null };
  gaps: string[]; assumptions: string[]; notes: string[];
  reports: { history_md: string; backtest_md: string; baseline_md: string };
}

// ---------- state ----------
const state = {
  info: null as AppInfo | null,
  files: new Map<string, File>(),
  market: "" as string,               // slug or "__other"
  other: { name: "", currency: "EUR", market: "", submarket: "" },
  result: null as Result | null,
  tab: "overview",
  fcMode: "growth" as "growth" | "level",
  settings: {} as Record<string, unknown>,
  customDrivers: [] as any[],
  driverEdits: {} as Record<string, { enabled?: boolean; probability?: number }>,
  fileCards: [] as any[],
  rerunning: false,
  running: false,
  error: "" as string,
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
  h("header", { class: "top" },
    h("h1", {}, "Hotel Market Forecast"),
    statusEl,
    h("span", { class: "spacer" }),
    h("button", { class: "btn", type: "button", onclick: toggleTheme, "aria-label": "Switch light or dark theme" }, "Theme"),
  ),
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
  { id: "drivers", label: "Drivers", hint: "drivers.yaml from a forecast run, or your own cards (YAML or JSON)", required: false, enabled: true, accept: ".yaml,.yml,.json" },
  { id: "airport", label: "Airline and airport data", hint: "Format to be added once an example is provided", required: false, enabled: false },
  { id: "reference", label: "Country performance", hint: "Format to be confirmed once an example is provided", required: false, enabled: false },
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
      else if (m) {
        state.market = "__other";
        state.other = { ...state.other, name: s ? `${m}, ${s}` : m, market: m, submarket: s ?? "" };
      }
    } catch { /* the run will report problems with the file */ }
  }
  renderInputs();
}

function slotView(s: Slot) {
  const f = state.files.get(s.id);
  const inputId = `file-${s.id}`;
  const input = h("input", {
    type: "file", id: inputId, accept: s.accept ?? ".xlsx,.xlsm,.csv", disabled: !s.enabled,
    onchange: (e: Event) => onFile(s.id, (e.target as HTMLInputElement).files?.[0]),
  });
  const box = h("div", { class: `slot${f ? " has-file" : ""}${s.enabled ? "" : " disabled"}`, "data-slot": s.id },
    h("div", { class: "row" }, h("span", { class: "label" }, s.label), s.required ? h("span", { class: "req" }, "required") : h("span", { class: "req" }, "optional")),
    h("div", { class: "hint" }, s.hint),
    f
      ? h("div", { class: "file" }, h("span", { class: "name" }, f.name), h("button", { class: "btn link", type: "button", onclick: () => { state.files.delete(s.id); renderInputs(); } }, "Remove"))
      : s.enabled ? h("div", { class: "pick" }, h("label", { for: inputId, class: "btn", tabindex: "0",
          onkeydown: (e: KeyboardEvent) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); } } }, "Choose file"),
          h("span", { class: "small muted" }, " or drop it here")) : null,
    input,
  );
  if (s.enabled) {
    box.addEventListener("dragover", (e) => { e.preventDefault(); box.classList.add("drag"); });
    box.addEventListener("dragleave", () => box.classList.remove("drag"));
    box.addEventListener("drop", (e) => { e.preventDefault(); box.classList.remove("drag"); onFile(s.id, e.dataTransfer?.files?.[0]); });
  }
  return box;
}

function marketChoice(): MarketChoice {
  if (state.market && state.market !== "__other") return { slug: state.market };
  const o = state.other;
  return { adhoc: { name: o.name || "Unnamed market", currency: o.currency || "EUR", costar_market: o.market || undefined, costar_submarket: o.submarket || undefined } };
}

function renderInputs() {
  const slots = state.info?.slots ?? DEFAULT_SLOTS;
  const ready = !!state.info;
  const missing = slots.filter((s) => s.required && !state.files.has(s.id)).map((s) => s.label.toLowerCase());
  const canRun = ready && !missing.length && !state.running && (state.market !== "__other" || !!state.other.name);
  const other = state.market === "__other";
  inputsEl.replaceChildren(
    h("p", { class: "privacy" }, "Files are read and processed in this browser. Nothing is uploaded or stored on a server."),
    h("h2", {}, "Data"),
    h("div", { style: "margin-top:10px" }, slots.map(slotView)),
    h("div", { class: "field" },
      h("label", { for: "market" }, "Market configuration"),
      h("select", { id: "market", disabled: !ready, onchange: (e: Event) => { state.market = (e.target as HTMLSelectElement).value; renderInputs(); } },
        (state.info?.markets ?? []).map((m) => h("option", { value: m.slug, selected: state.market === m.slug }, `${m.name} (${m.currency})`)),
        h("option", { value: "__other", selected: other }, "Other market"),
      ),
      other ? h("div", { class: "row2" },
        h("input", { type: "text", "aria-label": "Market name", placeholder: "Market name", value: state.other.name,
          oninput: (e: Event) => { state.other.name = (e.target as HTMLInputElement).value; } , onchange: () => renderInputs() }),
        h("input", { type: "text", "aria-label": "Currency code", placeholder: "EUR", maxlength: "3", value: state.other.currency,
          oninput: (e: Event) => { state.other.currency = (e.target as HTMLInputElement).value.toUpperCase(); } }),
      ) : null,
      other ? h("div", { class: "small muted", style: "margin-top:6px" }, "Currency is the local currency of the export. CoStar files do not state it.") : null,
    ),
    h("div", { class: "run-row" },
      h("button", { class: "btn primary", type: "button", disabled: !canRun, onclick: run }, state.running ? "Running…" : "Run"),
      h("span", { class: "msg" }, !ready ? "Waiting for the engine" : missing.length ? `Add ${missing.join(" and ")}` : ""),
    ),
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
    state.tab = state.result.forecast ? "forecast" : "overview";
  } catch (e) {
    state.error = e instanceof Error ? e.message : String(e);
    state.result = null;
  } finally {
    state.running = false;
    renderInputs();
    renderResults();
  }
}

// ---------- results ----------
const TABS: [string, string][] = [
  ["overview", "Overview"],
  ["forecast", "Forecast"],
  ["history", "History"],
  ["backtest", "Backtest"],
  ["assumptions", "Assumptions"],
  ["pipeline", "Pipeline and supply"],
  ["checks", "Checks"],
  ["guardrails", "Guardrails"],
  ["mapping", "Mapping"],
  ["report", "Reports"],
];
const needsForecast = new Set(["forecast", "history", "backtest", "assumptions"]);

function badge(status: string, text?: string) {
  const label = text ?? ({ pass: "Pass", warn: "Warning", fail: "Fail", info: "Info" } as Record<string, string>)[status] ?? status;
  return h("span", { class: `badge ${status}` }, label);
}

function table(head: (string | [string, string])[], rows: (Node | string)[][], rowClass?: (i: number) => string) {
  return h("div", { class: "table-wrap" }, h("table", {},
    h("thead", {}, h("tr", {}, head.map((c) => Array.isArray(c) ? h("th", { class: c[1], scope: "col" }, c[0]) : h("th", { scope: "col" }, c)))),
    h("tbody", {}, rows.map((r, i) => h("tr", { class: rowClass?.(i) ?? "" }, r.map((c, j) => {
      const hc = head[j];
      return h("td", { class: Array.isArray(hc) ? hc[1] : "" }, c);
    })))),
  ));
}

function renderEmpty() {
  resultsEl.replaceChildren(h("div", { class: "empty" },
    h("h2", {}, "How it works"),
    h("ol", {},
      h("li", {}, "Drop the CoStar DataTable export (monthly market performance) and the CoStar pipeline property export."),
      h("li", {}, "Pick the market configuration. A new market is detected from the pipeline file."),
      h("li", {}, "Run. The engine maps the columns, checks the data, finds anomalies such as series breaks, and shows what the model will fit on."),
    ),
    h("p", {}, "The forecast is a provisional baseline built from the market's own history and pipeline. Forward-looking drivers and scenarios arrive in later phases. Airline and country performance inputs open once their formats are added."),
    state.error ? h("div", { class: "error-box" }, `The run failed: ${state.error}`) : null,
  ));
}

function renderResults() {
  disposeCharts();
  const r = state.result;
  if (!r) { renderEmpty(); return; }
  const allChecks = Object.values(r.checks).flat();
  const nWarn = allChecks.filter((c) => c.status === "warn").length;
  const nFail = allChecks.filter((c) => c.status === "fail").length;
  const lastFull = [...r.annual].reverse().find((a) => a.complete);
  const prevFull = lastFull ? r.annual.find((a) => a.year === lastFull.year - 1) : undefined;

  const tabs = h("nav", { class: "tabs", role: "tablist", "aria-label": "Result views" },
    TABS.map(([id, label]) => [id, label, !needsForecast.has(id) || !!r.forecast] as const).map(([id, label, enabled]) => h("button", {
      role: "tab", type: "button", id: `tab-${id}`, "aria-selected": String(state.tab === id), "aria-controls": "panel",
      disabled: !enabled, title: enabled ? undefined : (r.forecast_error ? "The forecast failed; see Overview" : "Needs data that passes validation"),
      onclick: () => { state.tab = id; renderResults(); },
    }, id === "assumptions" && changedCount() ? `${label} (${changedCount()} changed)` : label)));
  tabs.addEventListener("keydown", (e) => {
    const ids = TABS.map((t) => t[0]).filter((id) => !needsForecast.has(id) || !!r.forecast);
    const i = ids.indexOf(state.tab);
    if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
      state.tab = ids[(i + (e.key === "ArrowRight" ? 1 : ids.length - 1)) % ids.length];
      renderResults();
      document.getElementById(`tab-${state.tab}`)?.focus();
    }
  });

  const panel = h("div", { class: "panel", id: "panel", role: "tabpanel", "aria-labelledby": `tab-${state.tab}` });
  resultsEl.replaceChildren(
    h("div", { class: "summary" },
      h("span", { class: "title" }, r.meta.market),
      r.meta.passed ? badge("pass", "Data passed validation") : badge("fail", "Data failed validation"),
      nWarn ? badge("warn", `${nWarn} warning${nWarn > 1 ? "s" : ""}`) : null,
      nFail ? badge("fail", `${nFail} failed check${nFail > 1 ? "s" : ""}`) : null,
    ),
    r.fatal.length ? h("div", { class: "error-box" }, "The run stopped:", h("ul", {}, r.fatal.map((f) => h("li", {}, f)))) : "",
    r.forecast_error ? h("div", { class: "error-box" }, `The forecast could not run: ${r.forecast_error}`) : "",
    r.drivers_error ? h("div", { class: "error-box" }, `The drivers file could not be read: ${r.drivers_error}`) : "",
    h("div", { class: "facts" },
      h("div", {}, h("span", {}, "History"), r.meta.first_month && r.meta.last_month ? `${r.meta.first_month} to ${r.meta.last_month}` : "none"),
      r.meta.stub_year ? h("div", {}, h("span", {}, "Stub year"), `${r.meta.stub_year}: ${r.meta.stub_actual_months} actual months`) : null,
      lastFull ? h("div", {}, h("span", {}, `${lastFull.year} occupancy`), h("span", { class: "num", style: "color:inherit" }, pct(lastFull.occupancy)),
        prevFull ? h("span", { class: "num muted" }, ` ${pts(lastFull.occupancy - prevFull.occupancy)}`) : null) : null,
      lastFull ? h("div", {}, h("span", {}, `${lastFull.year} ADR`), h("span", { class: "num", style: "color:inherit" }, `${num(lastFull.adr, 1)} ${r.meta.currency}`),
        prevFull ? h("span", { class: "num muted" }, ` ${signedPct(lastFull.adr / prevFull.adr - 1)}`) : null) : null,
      h("div", {}, h("span", {}, "Run"), h("code", { class: "small" }, r.meta.run_id)),
    ),
    tabs,
    panel,
    h("footer", { class: "foot" }, `Engine ${r.meta.engine_version}. Python runtime: Pyodide ${PYODIDE_VERSION}. Computed in this browser on ${r.meta.date}.`),
  );
  ({ overview, forecast: forecastView, history: historyView, backtest: backtestView, assumptions: assumptionsView, checks, guardrails, pipeline, mapping, report } as Record<string, (p: HTMLElement, r: Result) => void>)[state.tab]?.(panel, r);
}

function overview(p: HTMLElement, r: Result) {
  if (!r.annual.length) { p.append(h("p", {}, "No performance data was read.")); return; }
  const cur = r.meta.currency;
  const annualEl = h("div", { class: "chart", role: "img", "aria-label": "Annual occupancy and ADR" });
  const monthlyEl = h("div", { class: "chart", role: "img", "aria-label": "Monthly occupancy and ADR" });
  const breakYears = r.breaks.filter((b) => b.action === "exclude_growth").map((b) => Number(b.start.slice(0, 4)));
  p.append(
    h("h2", {}, "Annual history"),
    h("p", { class: "note" }, "Occupancy and ADR by calendar year, from the monthly export. The last year is year to date.",
      breakYears.length ? " Dashed lines mark detected series breaks; see Guardrails." : ""),
    annualEl,
    h("h2", {}, "Monthly history"),
    monthlyEl,
    h("h2", {}, "Annual table"),
    table(["Year", ["Months", "n"], ["Supply (room nights)", "n"], ["Demand (room nights)", "n"], ["Occupancy", "n"], ["Occ. change", "n"],
      [`ADR (${cur})`, "n"], ["ADR growth", "n"], [`RevPAR (${cur})`, "n"], ["RevPAR growth", "n"]],
      r.annual.map((a, i) => {
        const prev = i > 0 ? r.annual[i - 1] : undefined;
        const ok = prev && a.complete && prev.complete;
        return [a.complete ? String(a.year) : `${a.year} YTD`, String(a.months_observed), num(a.supply), num(a.demand), pct(a.occupancy),
          ok ? pts(a.occupancy - prev!.occupancy) : "", num(a.adr, 1), ok ? signedPct(a.adr / prev!.adr - 1) : "",
          num(a.revpar, 1), ok ? signedPct(a.revpar / prev!.revpar - 1) : ""];
      }), (i) => (r.annual[i].complete ? "" : "partial")),
    h("p", { class: "note" }, "Changes are shown only between complete years. The stub year is handled by the forecast engine."),
  );
  requestAnimationFrame(() => {
    annualChart(annualEl, r.annual, cur, breakYears);
    monthlyChart(monthlyEl, r.monthly, cur);
  });
}

function checks(p: HTMLElement, r: Result) {
  for (const [group, cs] of Object.entries(r.checks)) {
    p.append(h("h2", {}, group === "performance" ? "Market performance" : group === "pipeline" ? "Pipeline" : group));
    p.append(table(["ID", "Result", "Check", "Finding"], cs.map((c) => [
      c.id, badge(c.status), c.title,
      h("div", {}, c.message, c.details.length ? h("details", {}, h("summary", {}, `${c.details.length} detail${c.details.length > 1 ? "s" : ""}`),
        h("ul", { class: "details" }, c.details.map((d) => h("li", {}, d)))) : null),
    ])));
  }
}

function guardrails(p: HTMLElement, r: Result) {
  p.append(h("h2", {}, "Level breaks"),
    h("p", { class: "note" }, "A level break is a step that hits every month of a year in the same direction and does not reverse. It usually signals a change in the CoStar sample or definitions, not a market event. Growth that contains a confirmed break is left out of model fitting, and earlier levels are rescaled so ranges compare like with like."));
  if (!r.breaks.length) p.append(h("p", {}, "None detected."));
  else p.append(h("ul", {}, r.breaks.map((b) => h("li", {}, b.description, b.note ? ` ${b.note}` : ""))));

  p.append(h("h2", {}, "Growth observations used for fitting"),
    h("p", { class: "note" }, "Struck-through values are excluded. COVID years are excluded by default and tested in a sensitivity run."));
  const g = r.growth_sample;
  const cell = (row: Record<string, unknown>, m: string) => {
    const v = row[`g_${m}`] as number | null;
    if (v === null || v === undefined || row[`reason_${m}`] === "partial year") return h("span", {}, "");
    return h("span", { class: row[`use_${m}`] ? "" : "faint", style: row[`use_${m}`] ? "" : "text-decoration:line-through" }, signedPct(Math.expm1(v)));
  };
  p.append(table(["Year", ["Demand", "n"], ["Occupancy", "n"], ["ADR", "n"], "Excluded because"],
    g.map((row) => [String(row.year), cell(row, "demand"), cell(row, "occupancy"), cell(row, "adr"),
      [...new Set(["demand", "occupancy", "adr"].map((m) => String(row[`reason_${m}`] ?? "")).filter(Boolean))].join("; ")])));

  p.append(h("h2", {}, "Influence check"),
    h("p", { class: "note" }, "Each year is left out in turn. A year is flagged when it moves the average growth more than 3 times as much as a typical year does. Flagged years stay in; the model is fitted with and without them."));
  const flags = Object.entries(r.influence).flatMap(([m, v]) => v.flagged.map((f) => `${m}: ${f}`));
  p.append(flags.length ? h("ul", {}, flags.map((f) => h("li", {}, f))) : h("p", {}, "No year is flagged."));
}

function pipeline(p: HTMLElement, r: Result) {
  const rows = [...r.pipeline].sort((a, b) => (a.projected_open ?? "9999").localeCompare(b.projected_open ?? "9999"));
  const total = rows.reduce((s, x) => s + x.rooms, 0);
  const byYear = new Map<string, number>();
  rows.forEach((x) => { const y = x.projected_open?.slice(0, 4) ?? "Unknown"; byYear.set(y, (byYear.get(y) ?? 0) + x.rooms); });
  p.append(
    h("h2", {}, `Projects (${rows.length}, ${num(total)} rooms)`),
    table(["Project", ["Rooms", "n"], "Stage", "Type", "Projected open", "Class", "Brand"], rows.map((x) => [
      x.name, num(x.rooms), x.stage.replace(/_/g, " "), x.project_type.replace(/_/g, " "),
      x.projected_open ? x.projected_open.slice(0, 7) + (x.projected_open_basis === "year_only_default_month" ? " (month assumed)" : "") : "not given",
      x.chain_scale ?? "", x.brand ?? "",
    ])),
    h("p", { class: "note" }, "Renovation reopenings are hotels already in inventory but out of supply while closed. They add supply when they reopen. Delivery probabilities and delays are applied in the supply phase."),
    h("h2", {}, "Rooms by projected opening year"),
    table(["Year", ["Rooms", "n"]], [...byYear.entries()].sort().map(([y, n]) => [y, num(n)])),
  );
  const f = r.forecast;
  if (!f) return;
  const years = f.summary.map((x) => String(x.year));
  p.append(
    h("h2", {}, "Supply outlook"),
    h("p", { class: "note" }, `Starting from ${num(f.supply.base_available_rooms)} available rooms in ${f.meta.last_actual_month} (temporarily closed hotels are out of supply until they reopen). Opening probabilities and delays are assumptions until pipeline snapshots are available.`),
    table(["Year", ["Supply growth, mean", "n"], ["P10", "n"], ["P50", "n"], ["P90", "n"]], f.summary.map((x) => [
      String(x.year), signedPct(x.supply_growth.mean), signedPct(x.supply_growth.p10), signedPct(x.supply_growth.p50), signedPct(x.supply_growth.p90)])),
    h("h2", {}, "Opening probabilities"),
    table(["Project", ["Rooms", "n"], "Stage", "Planned", "Median opening", ...years.map((y) => [`Open by ${y}`, "n"] as [string, string])],
      f.supply.projects.map((x) => [x.name, num(x.rooms), String(x.stage).replace(/_/g, " "), x.projected_open ?? "", x.median_open ?? "not within horizon",
        ...years.map((y) => pct(x[y], 0))])),
  );
}

function mapping(p: HTMLElement, r: Result) {
  for (const f of r.files) {
    p.append(h("h2", {}, f.filename),
      h("p", { class: "note" }, f.mapping ? `Mapping ${f.mapping}, sheet ${f.sheet}, ${f.rows} rows${f.snapshot_date ? `, snapshot ${f.snapshot_date}` : ""}.` : f.note));
    if (f.mapped.length) p.append(table(["Source column", "Internal field"], f.mapped.map(([a, b]) => [a, h("code", {}, b)])));
    if (f.ignored.length) p.append(h("details", {}, h("summary", {}, `${f.ignored.length} columns not used`), h("p", { class: "small muted" }, f.ignored.join(", "))));
  }
}

function report(p: HTMLElement, r: Result) {
  p.append(
    h("div", { class: "downloads" },
      h("button", { class: "btn", type: "button", onclick: () => download(`validation-${r.meta.slug}-${r.meta.run_id}.md`, r.validation_md, "text/markdown") }, "Download validation.md"),
      h("button", { class: "btn", type: "button", onclick: () => download(`results-${r.meta.slug}-${r.meta.run_id}.json`, JSON.stringify(r, null, 2), "application/json") }, "Download results.json"),
      r.forecast ? h("button", { class: "btn", type: "button", onclick: () => download(`baseline-${r.meta.slug}-${r.meta.run_id}.md`, r.forecast!.reports.baseline_md, "text/markdown") }, "Download baseline.md") : null,
      r.forecast ? h("button", { class: "btn", type: "button", onclick: () => download(`history-${r.meta.slug}-${r.meta.run_id}.md`, r.forecast!.reports.history_md, "text/markdown") }, "Download history.md") : null,
      r.forecast ? h("button", { class: "btn", type: "button", onclick: () => download(`backtest-${r.meta.slug}-${r.meta.run_id}.md`, r.forecast!.reports.backtest_md, "text/markdown") }, "Download backtest.md") : null,
    ),
    h("p", { class: "note" }, "Downloads are created in the browser. They contain figures derived from your CoStar files, so treat them as licensed data."),
    h("pre", { class: "md" }, r.validation_md),
  );
}

const MEAS: Record<string, string> = { occ_growth: "Occupancy growth", occ_pts: "Occupancy change", adr_growth: "ADR growth",
  revpar_growth: "RevPAR growth", demand_growth: "Demand growth", supply_growth: "Supply growth" };

function q3(qq: Q, f: (x: number) => string) {
  return h("span", {}, h("b", {}, f(qq.p50)), h("br"), h("span", { class: "muted small" }, `${f(qq.p10)} to ${f(qq.p90)}`));
}

function forecastView(p: HTMLElement, r: Result) {
  const f = r.forecast!;
  const cur = f.meta.currency;
  p.append(
    h("div", { class: "privacy", style: "margin-bottom:14px" },
      `${f.meta.status}. Built from this market's history and pipeline only. ADR is in ${f.meta.adr_mode} mode`,
      f.meta.adr_mode === "nominal" ? " because the CPI history is not loaded" : " (inflation plus pricing power)", ". Model variant chosen by backtest: ", h("code", {}, f.meta.variant),
      `. ${num(f.meta.paths)} paths, seed ${f.meta.seed}, computed in ${f.meta.seconds} s.`),
    h("h2", {}, "Headline"),
    h("p", { class: "note" }, "P50 in bold, then the P10 to P90 range. Growth is year over year; the stub year compares the full year (actual plus simulated months) with the prior full year."),
    table(["Year", ["Occupancy growth", "n"], ["Occupancy change", "n"], ["ADR growth", "n"], ["RevPAR growth", "n"], ["Mean RevPAR growth", "n"], ["P(RevPAR down)", "n"]],
      f.summary.map((x) => [`${x.year} ${x.label}`, q3(x.occ_growth, signedPct), q3(x.occ_pts, pts), q3(x.adr_growth, signedPct),
        q3(x.revpar_growth, signedPct), signedPct(x.revpar_growth.mean), pct(x.p_revpar_negative, 0)])),
    table(["Year", ["Occupancy, P50", "n"], [`ADR (${cur}), P50`, "n"], [`RevPAR (${cur}), P50`, "n"]],
      f.summary.map((x) => [String(x.year), pct(x.occupancy.p50), num(x.adr.p50, 1), num(x.revpar.p50, 1)])),
  );
  if (f.adjustments?.length) {
    p.append(h("div", { class: "flags" }, h("div", { class: "flag" }, badge("warn", "Adjusted"),
      " This run uses your settings, not only the model's defaults: ", f.adjustments.join("; "), ".")));
  }
  if (f.drivers?.applied?.length) driversSummary(p, f);
  if (f.explanation) whyView(p, f);
  const toggle = h("div", { class: "downloads", role: "group", "aria-label": "Chart values" },
    (["growth", "level"] as const).map((m) => h("button", { class: `btn${state.fcMode === m ? " primary" : ""}`, type: "button", "aria-pressed": String(state.fcMode === m),
      onclick: () => { state.fcMode = m; renderResults(); } }, m === "growth" ? "Growth %" : "Levels")));
  p.append(h("h2", {}, "Forecast against history"), toggle);
  const raw = r.annual.filter((a) => a.complete);
  const specs = state.fcMode === "growth"
    ? [["Occupancy growth", "occ_growth", "pct", "%"], ["ADR growth", "adr_growth", "pct", "%"], ["RevPAR growth", "revpar_growth", "pct", "%"]] as const
    : [["Occupancy", "occupancy", "level_pct", "%"], [`ADR (${cur})`, "adr", "money", cur], [`RevPAR (${cur})`, "revpar", "money", cur]] as const;
  const charts: (() => void)[] = [];
  for (const [title, key, kind, unit] of specs) {
    const el = h("div", { class: "chart", role: "img", "aria-label": `${title}: history and forecast fan` });
    p.append(h("h3", { style: "margin-top:12px" }, title), el);
    const hist = raw.map((a, i) => {
      if (state.fcMode === "level") return { year: a.year, value: (a as any)[key] as number };
      const prev = i > 0 && raw[i - 1].year === a.year - 1 ? raw[i - 1] : null;
      const base = key === "occ_growth" ? "occupancy" : key === "adr_growth" ? "adr" : "revpar";
      return { year: a.year, value: prev ? (a as any)[base] / (prev as any)[base] - 1 : null };
    });
    const fq = f.summary.map((x) => ({ year: x.year, label: x.label, q: (x as any)[key] as Q }));
    let yRange: [number, number] | undefined;
    if (state.fcMode === "growth") {
      // Fit the axis to the forecast band and non-COVID history; COVID swings run off the scale.
      // Leave out COVID years and years the guardrails exclude (series breaks) when fitting the axis.
      const flag = key === "occ_growth" ? "use_occ" : "use_adr";
      const skip = new Set((f.history.annual as any[]).filter((a) => a.covid || !a[flag]).map((a) => a.year));
      const vals = [...hist.filter((x) => x.value !== null && !skip.has(x.year)).map((x) => x.value as number),
        ...fq.flatMap((x) => [x.q.p10, x.q.p90])];
      const lo = Math.min(...vals), hi = Math.max(...vals), padv = (hi - lo) * 0.08;
      yRange = [Math.floor((lo - padv) * 100) / 100, Math.ceil((hi + padv) * 100) / 100];
    }
    charts.push(() => fanChart(el, { history: hist, forecast: fq, kind, unit, yRange }));
  }
  p.append(h("p", { class: "note" }, "History shows the values as reported, including the 1999 ADR step that the model excludes.",
    state.fcMode === "growth" ? " The growth axes are fitted to the forecast and to years the model uses, so the 2020 to 2022 swings and the 1999 step run off the scale." : "",
    " Shaded bands are P10 to P90 and P25 to P75; the line is P50 and the diamond is the mean."));

  if (f.stub) {
    const st = f.stub;
    const el = h("div", { class: "chart", role: "img", "aria-label": "Stub year months, actual and simulated" });
    const prior = r.monthly.filter((m) => m.period.startsWith(String(f.meta.base_year)));
    p.append(h("h2", {}, `Stub year ${f.meta.stub_year}: actual and simulated months`),
      h("p", { class: "note" }, `${st.actual.length} actual months. The remaining months take the prior year's months times the recency-weighted year-to-date trend (demand ${signedPct(st.trend_demand)}, ADR ${signedPct(st.trend_adr)}), with errors drawn from how that rule did in ${st.calibration_years.length} past years at the same cutoff.`),
      el);
    charts.push(() => stubChart(el, { prior: prior.map((m) => ({ period: m.period, value: m.occupancy })), actual: st.actual.map((a) => ({ period: a.period, value: a.occupancy })),
      simulated: st.simulated.map((x) => ({ period: String(x.period), p10: Number(x.occ_p10), p50: Number(x.occ_p50), p90: Number(x.occ_p90) })), kind: "pct", unit: "Occupancy (%)" }));
  }

  p.append(h("h2", {}, "Against the market's history"),
    h("p", { class: "note" }, "Where each year's P50 growth falls in this market's own record outside COVID years, and the past year that looks most like it."),
    table(["Year", ["Occ. growth percentile", "n"], ["ADR growth percentile", "n"], ["RevPAR growth percentile", "n"], "Nearest analog", "Flags"],
      f.positions.map((x) => {
        const flags = (["occ_growth", "adr_growth", "revpar_growth"] as const).filter((k) => x[k].outside_p5_p95).map((k) => `${MEAS[k]} outside history P5 to P95`);
        if (x.occ_above_hist_max) flags.push("occupancy above historical maximum");
        return [String(x.year), pct(x.occ_growth.percentile, 0), pct(x.adr_growth.percentile, 0), pct(x.revpar_growth.percentile, 0), String(x.analog_year ?? ""), flags.join("; ") || "none"];
      })));

  const d = f.models.demand, a = f.models.adr;
  p.append(h("h2", {}, "Models"),
    h("ul", {},
      h("li", {}, `Demand in normal years (${d.n_normal}): long-run growth ${signedPct(d.mu)} (data estimate ${signedPct(d.mu_hat)}, weight on data ${num(d.w_mu, 2)}), persistence ${num(d.phi, 2)}, noise ${pct(d.sigma)} a year, ${String(d.residual_type).replace("_", "-")} residuals.`),
      h("li", {}, `Shocks found in history: ${(d.shock_years as number[]).join(", ") || "none"}. Annual probability: moderate ${pct(d.p_moderate)}, severe ${pct(d.p_severe)}.`),
      h("li", {}, `ADR (${a.mode} mode): drift ${signedPct(a.alpha)} a year, pricing power ${num(a.beta, 2)} (ADR moves ${num(a.beta, 2)}% per 1% occupancy change), noise ${pct(a.sigma_u)}, correlation with demand ${num(a.corr, 2)}.`),
      h("li", {}, `Occupancy ceiling ${pct(f.models.ceiling.annual)}. ${f.models.ceiling.basis}`),
      f.macro?.cpi_path && f.macro.cpi_source ? h("li", {}, "Inflation path used for ADR: ",
        Object.entries(f.macro.cpi_path).map(([y, v]) => `${y} ${pct(v)}`).join(", "), ". Source: ",
        h("a", { href: f.macro.cpi_source.url, target: "_blank", rel: "noopener" }, `${f.macro.cpi_source.publisher}, ${f.macro.cpi_source.title}`),
        `, accessed ${f.macro.cpi_source.accessed}. `, f.macro.cpi_projection_note ?? "") : null,
    ));
  const cs = f.covid_sensitivity;
  p.append(h("h2", {}, "COVID-years sensitivity"),
    h("p", { class: "note" }, `Default: ${cs.default} 2020 to 2022 from estimation. Difference in P50 between the default and the alternative.`),
    table(["Year", ["Occupancy growth", "n"], ["ADR growth", "n"], ["RevPAR growth", "n"]],
      cs.by_year.map((x) => [String(x.year), signedPct(x.occ_growth.difference), signedPct(x.adr_growth.difference), signedPct(x.revpar_growth.difference)])),
    h("h2", {}, "Gaps"), h("ul", {}, f.gaps.map((g) => h("li", {}, g))),
    h("h2", {}, "Assumptions"), h("ul", {}, f.assumptions.map((g) => h("li", {}, g))),
  );
  requestAnimationFrame(() => charts.forEach((fn) => fn()));
}

// ---------- settings, drivers and the Assumptions tab ----------
const storeKey = () => `hmf-settings:${state.result?.meta.slug ?? "market"}`;

function loadSaved() {
  try {
    const raw = localStorage.getItem(`hmf-settings:${state.market === "__other" ? (state.other.name || "market").toLowerCase().replace(/[^a-z0-9]+/g, "-") : state.market}`);
    if (raw) {
      const v = JSON.parse(raw);
      state.settings = v.settings ?? {};
      state.customDrivers = v.customDrivers ?? [];
      state.driverEdits = v.driverEdits ?? {};
    }
  } catch { /* storage unavailable */ }
}

function save() {
  try {
    localStorage.setItem(storeKey(), JSON.stringify({ settings: state.settings, customDrivers: state.customDrivers, driverEdits: state.driverEdits }));
  } catch { /* storage unavailable */ }
}

function changedCount(): number {
  const schema = state.result?.settings_schema ?? [];
  const defaults = new Map(schema.flatMap((g) => g.fields.map((f) => [f.key, f.default] as const)));
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

function autoValue(key: string, f: Forecast): number | null {
  const se = f.settings_effective ?? {};
  if (key === "supply.unannounced_rate") return se.unannounced_rate_used ?? null;
  if (key === "adr.pricing_power") return se.fitted?.adr_beta ?? null;
  if (key === "occupancy.ceiling") return se.fitted?.ceiling ?? null;
  return null;
}

function fmtSetting(kind: string, v: unknown): string {
  if (v === null || v === undefined) return "auto";
  if (typeof v === "boolean") return v ? "on" : "off";
  const n = Number(v);
  if (kind === "percent") return `${(n * 100).toFixed(1)}%`;
  if (kind === "points") return `${n >= 0 ? "+" : ""}${(n * 100).toFixed(1)} pts`;
  if (kind === "multiplier") return `x${n}`;
  if (kind === "months") return `${n} months`;
  return String(Math.round(n * 1000) / 1000);
}

function settingInput(fd: SettingField, f: Forecast) {
  const scale = fd.kind === "percent" || fd.kind === "points" ? 100 : 1;
  const cur = fd.key in state.settings ? state.settings[fd.key] : fd.default;
  const id = `set-${fd.key.replace(/\./g, "-")}`;
  if (fd.kind === "bool") {
    return h("input", { type: "checkbox", id, checked: Boolean(cur), "aria-label": fd.label,
      onchange: (e: Event) => { state.settings[fd.key] = (e.target as HTMLInputElement).checked; save(); renderResults(); } });
  }
  const auto = autoValue(fd.key, f);
  return h("input", { type: "number", id, class: "num-in", "aria-label": fd.label,
    step: String((fd.step ?? 0.01) * scale), min: fd.min !== undefined ? String(fd.min * scale) : undefined, max: fd.max !== undefined ? String(fd.max * scale) : undefined,
    value: cur === null || cur === undefined ? "" : String(Math.round(Number(cur) * scale * 1000) / 1000),
    placeholder: cur === null && auto !== null ? `auto ${fmtSetting(fd.kind, auto)}` : "auto",
    onchange: (e: Event) => {
      const raw = (e.target as HTMLInputElement).value.trim();
      if (raw === "") { if (fd.default === null) delete state.settings[fd.key]; else state.settings[fd.key] = fd.default; }
      else state.settings[fd.key] = Number(raw) / scale;
      save(); renderResults();
    } });
}

function assumptionsView(p: HTMLElement, r: Result) {
  const f = r.forecast!;
  const schema = r.settings_schema ?? [];
  const n = changedCount();
  p.append(
    h("p", {}, "Change any assumption below, then rerun. The forecast reruns in this browser on the data already loaded, in a few seconds. Every change is listed as a user adjustment in the forecast and in the reports, so it stays separate from the model's own estimates."),
    h("div", { class: "downloads sticky-actions" },
      h("button", { class: "btn primary", type: "button", disabled: state.rerunning, onclick: rerun }, state.rerunning ? "Rerunning…" : "Rerun forecast"),
      h("button", { class: "btn", type: "button", disabled: !n, onclick: () => { state.settings = {}; state.driverEdits = {}; state.customDrivers = []; save(); renderResults(); } }, "Reset to defaults"),
      h("button", { class: "btn", type: "button", onclick: () => download(`settings-${r.meta.slug}.json`, JSON.stringify({ settings: state.settings, driverEdits: state.driverEdits }, null, 2), "application/json") }, "Download settings"),
      h("button", { class: "btn", type: "button", onclick: () => download(`drivers-${r.meta.slug}.json`, JSON.stringify({ drivers: editedCards([...state.fileCards, ...state.customDrivers]) }, null, 2), "application/json") }, "Download drivers"),
      h("span", { class: "msg small muted" }, n ? `${n} change${n > 1 ? "s" : ""} from the defaults. Rerun to apply.` : "All values at their defaults."),
    ),
  );
  for (const g of schema) {
    p.append(h("h2", {}, g.title), h("p", { class: "note" }, g.help),
      table(["Setting", ["Value", "n"], ["Default", "n"], ""], g.fields.map((fd) => {
        const cur = fd.key in state.settings ? state.settings[fd.key] : fd.default;
        const changed = fd.key in state.settings && state.settings[fd.key] !== fd.default;
        return [h("label", { for: `set-${fd.key.replace(/\./g, "-")}` }, fd.label), settingInput(fd, f),
          fmtSetting(fd.kind, fd.default === null ? autoValue(fd.key, f) : fd.default) + (fd.default === null ? " (auto)" : ""),
          changed ? badge("warn", "changed") : (cur === null ? "" : "")];
      })));
  }
  driversEditor(p, f);
}

function driversSummary(p: HTMLElement, f: Forecast) {
  const d = f.drivers!;
  const att = new Map(d.attribution.map((a: any) => [a.id, a]));
  const years = f.summary.map((x) => x.year);
  p.append(h("h2", {}, "Drivers in this forecast"),
    h("p", { class: "note" }, "Each driver happens in a share of paths equal to its probability, with a size drawn from its range. The columns show how much it moves mean RevPAR growth in each year, measured by rerunning without it."),
    table(["Driver", ["Chance", "n"], ...years.map((y) => [String(y), "n"] as [string, string])],
      [...d.applied.map((a: any) => {
        const at: any = att.get(a.id);
        return [a.title, pct(a.probability, 0), ...years.map((_, j) => at ? signedPct(at.years[j].revpar_growth_mean) : "")];
      }),
      ...(att.has("all") ? [[h("b", {}, "All drivers together"), "", ...years.map((_, j) => h("b", {}, signedPct((att.get("all") as any).years[j].revpar_growth_mean)))]] : [])]));
}

function driversEditor(p: HTMLElement, f: Forecast) {
  const issues = f.drivers?.issues ?? [];
  const ladder = f.drivers?.ladder ?? {};
  const all = [...state.fileCards.map((c) => ({ c, src: c.origin === "user" ? "file (yours)" : "research" })), ...state.customDrivers.map((c) => ({ c, src: "added here" }))];
  p.append(h("h2", {}, "Drivers"),
    h("p", { class: "note" }, "Switch a driver off, or change its chance of happening. For events, the chance follows the status ladder unless you set it: ",
      Object.entries(ladder).map(([k, v]: [string, any]) => `${k.replace(/_/g, " ")} ${Math.round(v.default * 100)}%`).join(", "), "."));
  if (!all.length) p.append(h("p", {}, "No drivers loaded. Drop a drivers.yaml on the left, or add one below."));
  else p.append(table(["Include", "Driver", "Affects", "Year", ["Chance (%)", "n"], "Size if it happens", "Source", "Checks", ""],
    all.map(({ c, src }) => {
      const e = state.driverEdits[c.id] ?? {};
      const pr = e.probability ?? c.probability ?? (c.event_status ? ladder[c.event_status]?.default : null);
      const unit = c.magnitude.unit === "rooms" ? " rooms" : c.magnitude.unit === "multiplier" ? "x" : "%";
      const iss = issues.filter((i) => i.id === c.id && i.level !== "info");
      return [
        h("input", { type: "checkbox", checked: e.enabled !== false, "aria-label": `Include ${c.title}`,
          onchange: (ev: Event) => { state.driverEdits[c.id] = { ...e, enabled: (ev.target as HTMLInputElement).checked }; save(); renderResults(); } }),
        c.title, String(c.affects).replace(/_/g, " "), String(c.timing.start_year),
        h("input", { type: "number", class: "num-in", min: "0", max: "100", step: "5", "aria-label": `Chance for ${c.title}`,
          value: pr === null || pr === undefined ? "" : String(Math.round(pr * 1000) / 10),
          onchange: (ev: Event) => { const v = (ev.target as HTMLInputElement).value.trim(); state.driverEdits[c.id] = { ...e, probability: v === "" ? undefined : Number(v) / 100 }; save(); renderResults(); } }),
        `${c.magnitude.low} to ${c.magnitude.high}${unit} (most likely ${c.magnitude.mode}${unit})`, src,
        iss.length ? h("details", {}, h("summary", {}, `${iss.length}`), h("ul", { class: "details" }, iss.map((i) => h("li", {}, `${i.level}: ${i.text}`)))) : "ok",
        src === "added here" ? h("button", { class: "btn link", type: "button", onclick: () => { state.customDrivers = state.customDrivers.filter((x) => x.id !== c.id); save(); renderResults(); } }, "Remove") : "",
      ];
    })));

  // Add a driver
  const form = h("form", { class: "driver-form", onsubmit: (ev: Event) => {
    ev.preventDefault();
    const fd = new FormData(ev.target as HTMLFormElement);
    const g = (k: string) => String(fd.get(k) ?? "").trim();
    const affects = g("affects");
    const unit = affects === "supply" ? "rooms" : affects === "shock_probability" ? "multiplier" : "pct_of_level";
    const status = g("status");
    const prob = g("prob");
    const year = Number(g("year"));
    const decay = Number(g("decay") || 0);
    const card: any = {
      id: `user-${Date.now().toString(36)}`, title: g("title") || "Untitled driver", category: affects === "shock_probability" ? "geopolitical" : "events",
      affects, timing: { start_year: year, peak_year: year, decay_years: decay }, shape: g("shape"),
      magnitude: { low: Number(g("low")), mode: Number(g("mode")), high: Number(g("high")), unit },
      already_in_baseline: "none", baseline_overlap_note: g("overlap") || "Added by the user; judged not to be in the historical trend.",
      origin: "user", status: "approved", confidence: "low",
    };
    if (prob !== "") card.probability = Number(prob) / 100;
    if (status) card.event_status = status;
    if (!(card.magnitude.low <= card.magnitude.mode && card.magnitude.mode <= card.magnitude.high)) {
      alert("Size must satisfy low <= most likely <= high."); return;
    }
    state.customDrivers.push(card);
    save();
    renderResults();
  } },
    h("h3", {}, "Add an event or risk"),
    h("div", { class: "form-grid" },
      h("label", {}, "Title", h("input", { name: "title", required: true, placeholder: "e.g. Stadium concert series" })),
      h("label", {}, "Affects", h("select", { name: "affects" },
        h("option", { value: "demand" }, "Demand (percent of the year's demand)"), h("option", { value: "adr" }, "ADR (percent of the year's ADR)"),
        h("option", { value: "shock_probability" }, "Chance of a shock (multiplier)"), h("option", { value: "supply" }, "Supply (rooms)"))),
      h("label", {}, "Year", h("input", { name: "year", type: "number", required: true, value: String(f.summary[1]?.year ?? ""), min: String(f.summary[0].year), max: String(f.summary[f.summary.length - 1].year) })),
      h("label", {}, "Shape", h("select", { name: "shape" },
        h("option", { value: "one_off" }, "One-off (that year only)"), h("option", { value: "temporary" }, "Temporary (fades over the decay years)"), h("option", { value: "step" }, "Permanent from that year"))),
      h("label", {}, "Decay years", h("input", { name: "decay", type: "number", min: "0", max: "5", value: "0" })),
      h("label", {}, "Event status", h("select", { name: "status" }, h("option", { value: "" }, "not an event / not set"),
        Object.keys(ladder).map((k) => h("option", { value: k }, `${k.replace(/_/g, " ")} (${Math.round(ladder[k].default * 100)}%)`)))),
      h("label", {}, "Chance (%), blank to use the status", h("input", { name: "prob", type: "number", min: "0", max: "100", step: "5" })),
      h("label", {}, "Size: low", h("input", { name: "low", type: "number", step: "any", required: true, value: "0.5" })),
      h("label", {}, "Size: most likely", h("input", { name: "mode", type: "number", step: "any", required: true, value: "1" })),
      h("label", {}, "Size: high", h("input", { name: "high", type: "number", step: "any", required: true, value: "2" })),
      h("label", { class: "wide" }, "Why this is not already in the historical trend", h("input", { name: "overlap", placeholder: "e.g. a new event the city has not hosted before" })),
    ),
    h("p", { class: "note" }, "Sizes are percent for demand and ADR (1 means +1% of that year's level; use negatives for a fall), rooms for supply, and a multiplier for shock chance (2 doubles it). One-off events lift their year and the next year grows from a normal base."),
    h("button", { class: "btn", type: "submit" }, "Add driver"),
  );
  p.append(form);
}

function whyView(p: HTMLElement, f: Forecast) {
  const e = f.explanation!;
  const bp = (v: number | null | undefined) => (v === null || v === undefined || !Number.isFinite(v) ? "" : `${v >= 0 ? "+" : ""}${(v * 100).toFixed(1)}`);
  p.append(h("h2", {}, "Why the forecast sits here"));
  if (e.flags.length) {
    p.append(h("div", { class: "flags" }, e.flags.map((fl) => h("div", { class: "flag" }, badge("warn", "Check"), " ", fl.text))));
  } else {
    p.append(h("p", { class: "note" }, "No plausibility flags: each year's P50 sits inside the market's historical range, occupancy stays below the ceiling, and supply keeps pace with its history."));
  }
  p.append(h("h3", { style: "margin-top:12px" }, "What drives it"), h("ul", {}, e.drivers.map((d) => h("li", {}, d))));
  const c = e.cumulative_revpar;
  if (c && c.history_n) {
    p.append(h("p", {}, `Cumulative RevPAR growth ${c.from_year} to ${c.to_year}: P50 ${signedPct(c.forecast_p50)} (P10 ${signedPct(c.forecast_p10)}, P90 ${signedPct(c.forecast_p90)}). `,
      `The market's own ${c.history_n} five-year periods outside COVID ranged from ${signedPct(c.history_p10)} (P10) to ${signedPct(c.history_p90)} (P90), median ${signedPct(c.history_p50)}. `,
      h("b", {}, `The forecast P50 sits at the ${Math.round(c.percentile * 100)}th percentile.`)));
  }
  p.append(h("h3", { style: "margin-top:12px" }, "Year by year"), h("ul", {}, e.per_year.map((y) => h("li", {}, y.text))));
  const years = e.bridge.map((b) => String(b.year));
  const rows: [string, (b: any) => number | null, boolean?][] = [
    ["Demand: trend", (b) => b.demand.trend], ["Demand: momentum", (b) => b.demand.momentum],
    ["Demand: expected shocks", (b) => b.demand.shocks], ["Demand: drivers", (b) => b.demand.drivers ?? 0],
    ["Demand: new and reopened hotels", (b) => b.demand.new_hotels],
    ["Demand: occupancy ceiling", (b) => b.demand.ceiling], ["Demand total", (b) => b.demand.total, true],
    ["Supply: existing stock", (b) => b.supply.existing_level], ["Supply: removals", (b) => b.supply.removals],
    ["Supply: new builds", (b) => b.supply.new_builds], ["Supply: reopenings", (b) => b.supply.reopenings],
    ["Supply: unannounced", (b) => b.supply.unannounced], ["Supply: drivers", (b) => b.supply.drivers ?? 0],
    ["Supply total", (b) => b.supply.total, true],
    ["Occupancy (demand minus supply)", (b) => b.occupancy_total, true],
    ["ADR: inflation", (b) => b.adr.inflation], ["ADR: real drift", (b) => b.adr.real_drift],
    ["ADR: pricing power", (b) => b.adr.pricing_power], ["ADR: carry-over", (b) => b.adr.carry_over],
    ["ADR: drivers", (b) => b.adr.drivers ?? 0],
    ["ADR total", (b) => b.adr.total, true],
    ["Occupancy, years without a shock", (b) => b.calm?.occ_total ?? null, true], ["ADR, years without a shock", (b) => b.calm?.adr_total ?? null, true],
  ];
  p.append(h("details", { style: "margin-top:8px" }, h("summary", {}, "Growth bridge: every component by year (points of growth)"),
    table(["Component", ...years.map((y) => [y, "n"] as [string, string])],
      rows.map(([label, fn, bold]) => [bold ? h("b", {}, label) : label, ...e.bridge.map((b) => bold ? h("b", {}, bp(fn(b))) : bp(fn(b)))])),
    h("p", { class: "note" }, e.note)));
}

function historyView(p: HTMLElement, r: Result) {
  const hs = r.forecast!.history;
  const ex = hs.growth_percentiles;
  p.append(...hs.notes.map((n) => h("p", { class: "note" }, n)),
    h("h2", {}, "Growth distributions"),
    h("p", { class: "note" }, "Percentiles of annual growth. 'All' includes COVID years; 'ex-COVID' leaves them out. Years containing a series break are left out of both."),
    table(["Measure", "Sample", ["n", "n"], ["P5", "n"], ["P10", "n"], ["P25", "n"], ["P50", "n"], ["P75", "n"], ["P90", "n"], ["P95", "n"]],
      ex.map((x) => {
        const f = x.measure === "occ_pts" ? pts : signedPct;
        return [MEAS[x.measure] ?? x.measure, x.sample === "all" ? "All" : "Ex-COVID", String(x.n), ...[5, 10, 25, 50, 75, 90, 95].map((q) => f(x[`p${q}`]))];
      })),
    h("h2", {}, "Cycle map"),
    h("p", { class: "note" }, "Falls of 3% or more from a running peak in annual nominal RevPAR and demand."),
    table(["Measure", "Peak", "Trough", ["Depth", "n"], "Recovered", ["Years to recover", "n"], "Episode"],
      hs.cycles.map((x) => [x.measure === "revpar" ? "RevPAR" : "Demand", String(x.peak_year), String(x.trough_year), signedPct(x.depth),
        x.recovery_year === null ? "not yet" : String(x.recovery_year), x.years_to_recover === null ? "" : String(x.years_to_recover), x.episode])),
    h("h2", {}, "Supply and demand decomposition"),
    h("p", { class: "note" }, "Each year's change in occupancy (log) split into the part from demand and the part from supply."),
    table(["Year", ["Occupancy change", "n"], ["From demand", "n"], ["From supply", "n"]],
      hs.decomposition.map((x) => [String(x.year) + (x.covid ? " (COVID)" : ""), signedPct(x.dlog_occ), signedPct(x.from_demand), signedPct(x.from_supply)])),
    h("h2", {}, "Seasonality"),
    h("p", { class: "note" }, `Monthly share of annual demand and ADR index (month ADR divided by annual ADR), ${hs.seasonality_shift.early_years} against ${hs.seasonality_shift.recent_years}.`),
    table(["Month", ["Demand share, early", "n"], ["Demand share, recent", "n"], ["ADR index, early", "n"], ["ADR index, recent", "n"]],
      hs.seasonality.map((x) => [new Date(2000, x.month - 1, 1).toLocaleString("en-GB", { month: "short" }), pct(x.demand_share_early), pct(x.demand_share_recent),
        num(x.adr_index_early, 2), num(x.adr_index_recent, 2)])),
  );
}

function backtestView(p: HTMLElement, r: Result) {
  const b = r.forecast!.backtest;
  p.append(
    h("p", {}, `Rolling origins ${b.origins[0]} to ${b.origins[b.origins.length - 1]}: at each origin the models are refitted on data up to that year only, then the next five years are simulated and compared with what happened.`),
    h("ul", {}, b.notes.map((n) => h("li", { class: "note" }, n))),
    h("h2", {}, "Model variants"),
    h("p", { class: "note" }, "CRPS measures the whole forecast distribution against the outcome (lower is better). Coverage is the share of outcomes inside the P10 to P90 band (target 80%)."),
    table(["Variant", ["CRPS, all outcomes", "n"], ["Coverage, all", "n"], ["CRPS, ex-COVID outcomes", "n"], ["Coverage, ex-COVID", "n"]],
      b.variants.map((x) => [x.variant + (x.variant === b.chosen ? " (chosen)" : ""), num(x.crps_all, 4), pct(x.cov80_all, 0), num(x.crps_ex_covid, 4), pct(x.cov80_ex_covid, 0)])),
    h("p", { class: "note" }, b.calibrated && b.spread_by_h
      ? `Bands widened by years ahead (multiplier on the model's noise): ${b.spread_by_h.map((k, i) => `${i + 1}: ${k}`).join(", ")}. Coverage below is after widening; the multipliers were chosen on this backtest, so it is in-sample.`
      : "No band widening needed."),
    h("h2", {}, "Coverage by horizon"),
    h("p", { class: "note" }, "'Ex-COVID' drops outcome years 2020 to 2022. Bias is the median forecast minus the outcome, on average. Each cell rests on 12 to 19 outcomes, so treat differences of 10 points as noise."),
    table(["Measure", ["Years ahead", "n"], ["n", "n"], ["P10-P90, all", "n"], ["P10-P90, ex-COVID", "n"], ["P25-P75, ex-COVID", "n"], ["Bias, ex-COVID", "n"]],
      b.coverage.filter((x) => x.spread === b.spread).map((x) => [MEAS[x.measure], String(x.h), String(x.n), pct(x.cov80, 0), pct(x.cov80_ex, 0), pct(x.cov50_ex, 0), signedPct(x.bias_ex)])),
    h("h2", {}, "Against naive benchmarks"),
    h("p", { class: "note" }, "Ex-COVID outcomes. Model CRPS against the absolute error of two point forecasts: zero growth, and the historical mean growth known at the origin. For a point forecast CRPS equals absolute error, so the columns compare directly."),
    table(["Measure", ["Years ahead", "n"], ["Model CRPS", "n"], ["Model median abs. error", "n"], ["Zero growth abs. error", "n"], ["Historical mean abs. error", "n"]],
      b.benchmarks.filter((x) => x.outcomes === "ex_covid").map((x) => [MEAS[x.measure], String(x.h), pct(x.model_crps), pct(x.model_mae), pct(x.zero_mae), pct(x.mean_mae)])),
  );
}

renderInputs();
renderEmpty();
