import "./styles.css";
import { Engine, PYODIDE_VERSION, type AppInfo, type MarketChoice, type Slot } from "./engine";
import { annualChart, disposeCharts, monthlyChart } from "./charts";
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
}

// ---------- state ----------
const state = {
  info: null as AppInfo | null,
  files: new Map<string, File>(),
  market: "" as string,               // slug or "__other"
  other: { name: "", currency: "EUR", market: "", submarket: "" },
  result: null as Result | null,
  tab: "overview",
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
    type: "file", id: inputId, accept: ".xlsx,.xlsm,.csv", disabled: !s.enabled,
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
    state.result = (await engine.run(files, marketChoice())) as Result;
    state.tab = "overview";
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
const TABS: [string, string, boolean][] = [
  ["overview", "Overview", true],
  ["checks", "Checks", true],
  ["guardrails", "Guardrails", true],
  ["pipeline", "Pipeline", true],
  ["mapping", "Mapping", true],
  ["report", "Report", true],
  ["forecast", "Forecast", false],
];

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
    h("p", {}, "Forecast results appear here once the forecasting phases are built. Airline and country performance inputs open once their formats are added."),
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
    TABS.map(([id, label, enabled]) => h("button", {
      role: "tab", type: "button", id: `tab-${id}`, "aria-selected": String(state.tab === id), "aria-controls": "panel",
      disabled: !enabled, title: enabled ? undefined : "Available once the forecasting phases are built",
      onclick: () => { state.tab = id; renderResults(); },
    }, label)));
  tabs.addEventListener("keydown", (e) => {
    const ids = TABS.filter((t) => t[2]).map((t) => t[0]);
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
  ({ overview, checks, guardrails, pipeline, mapping, report } as Record<string, (p: HTMLElement, r: Result) => void>)[state.tab]?.(panel, r);
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
    ),
    h("p", { class: "note" }, "Downloads are created in the browser. They contain figures derived from your CoStar files, so treat them as licensed data."),
    h("pre", { class: "md" }, r.validation_md),
  );
}

renderInputs();
renderEmpty();
