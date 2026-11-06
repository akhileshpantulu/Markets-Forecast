import * as echarts from "echarts/core";
import { BarChart, LineChart } from "echarts/charts";
import { DataZoomComponent, GridComponent, LegendComponent, MarkLineComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";

echarts.use([BarChart, LineChart, GridComponent, TooltipComponent, LegendComponent, MarkLineComponent, DataZoomComponent, CanvasRenderer]);

const css = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

const live: echarts.ECharts[] = [];
window.addEventListener("resize", () => live.forEach((c) => c.resize()));

export function disposeCharts() {
  while (live.length) live.pop()!.dispose();
}

function base() {
  const text = css("--muted");
  const line = css("--line");
  return {
    textStyle: { fontFamily: "inherit", color: text },
    grid: { left: 56, right: 64, top: 56, bottom: 40 },
    legend: { top: 0, left: "center", textStyle: { color: text }, itemWidth: 14, itemHeight: 8 },
    tooltip: {
      trigger: "axis",
      backgroundColor: css("--surface"),
      borderColor: css("--line-strong"),
      textStyle: { color: css("--text"), fontSize: 12 },
    },
    axisCommon: { axisLine: { lineStyle: { color: line } }, splitLine: { lineStyle: { color: line } }, axisLabel: { color: text } },
  };
}

export interface AnnualRow { year: number; occupancy: number; adr: number; complete: boolean }
export interface MonthlyRow { period: string; occupancy: number; adr: number }

export function annualChart(el: HTMLElement, rows: AnnualRow[], currency: string, breakYears: number[]) {
  const b = base();
  const c = echarts.init(el);
  live.push(c);
  const years = rows.map((r) => (r.complete ? String(r.year) : `${r.year} YTD`));
  c.setOption({
    textStyle: b.textStyle,
    grid: b.grid,
    legend: b.legend,
    tooltip: {
      ...b.tooltip,
      valueFormatter: undefined,
      formatter: (ps: { seriesName: string; value: number; axisValue: string; marker: string }[]) =>
        [`<b>${ps[0].axisValue}</b>`, ...ps.map((p) =>
          `${p.marker}${p.seriesName}: ${p.seriesName.startsWith("Occ") ? (p.value * 100).toFixed(1) + "%" : p.value.toFixed(1) + " " + currency}`)].join("<br>"),
    },
    xAxis: { type: "category", data: years, ...b.axisCommon, splitLine: { show: false } },
    yAxis: [
      { type: "value", name: "Occupancy (%)", min: 0, max: 1, ...b.axisCommon, axisLabel: { color: b.textStyle.color, formatter: (v: number) => `${Math.round(v * 100)}` }, nameTextStyle: { color: b.textStyle.color } },
      { type: "value", name: `ADR (${currency})`, ...b.axisCommon, splitLine: { show: false }, nameTextStyle: { color: b.textStyle.color } },
    ],
    series: [
      { name: "Occupancy", type: "bar", data: rows.map((r) => r.occupancy), itemStyle: { color: css("--chart-3"), opacity: 0.55 }, barMaxWidth: 22 },
      {
        name: `ADR (${currency})`, type: "line", yAxisIndex: 1, data: rows.map((r) => r.adr), symbolSize: 5,
        lineStyle: { color: css("--chart-1"), width: 2 }, itemStyle: { color: css("--chart-1") },
        markLine: breakYears.length ? {
          symbol: "none", silent: true,
          lineStyle: { color: css("--warn"), type: "dashed" },
          label: { formatter: "Level break", color: css("--warn"), position: "insideEndTop" },
          data: breakYears.map((y) => ({ xAxis: String(y) })),
        } : undefined,
      },
    ],
  });
}

export function monthlyChart(el: HTMLElement, rows: MonthlyRow[], currency: string) {
  const b = base();
  const c = echarts.init(el);
  live.push(c);
  const start = Math.max(0, 100 - (72 / Math.max(rows.length, 1)) * 100);
  c.setOption({
    textStyle: b.textStyle,
    grid: { ...b.grid, bottom: 64 },
    legend: b.legend,
    tooltip: {
      ...b.tooltip,
      formatter: (ps: { seriesName: string; value: number; axisValue: string; marker: string }[]) =>
        [`<b>${ps[0].axisValue}</b>`, ...ps.map((p) =>
          `${p.marker}${p.seriesName}: ${p.seriesName.startsWith("Occ") ? (p.value * 100).toFixed(1) + "%" : p.value.toFixed(1) + " " + currency}`)].join("<br>"),
    },
    dataZoom: [{ type: "slider", start, end: 100, height: 18, bottom: 12 }, { type: "inside" }],
    xAxis: { type: "category", data: rows.map((r) => r.period), ...b.axisCommon, splitLine: { show: false } },
    yAxis: [
      { type: "value", name: "Occupancy (%)", min: 0, max: 1, ...b.axisCommon, axisLabel: { color: b.textStyle.color, formatter: (v: number) => `${Math.round(v * 100)}` }, nameTextStyle: { color: b.textStyle.color } },
      { type: "value", name: `ADR (${currency})`, ...b.axisCommon, splitLine: { show: false }, nameTextStyle: { color: b.textStyle.color } },
    ],
    series: [
      { name: "Occupancy", type: "line", data: rows.map((r) => r.occupancy), showSymbol: false, lineStyle: { color: css("--chart-3"), width: 1.5 }, itemStyle: { color: css("--chart-3") } },
      { name: `ADR (${currency})`, type: "line", yAxisIndex: 1, data: rows.map((r) => r.adr), showSymbol: false, lineStyle: { color: css("--chart-1"), width: 1.5 }, itemStyle: { color: css("--chart-1") } },
    ],
  });
}

export interface Quantiles { mean: number; p10: number; p25: number; p50: number; p75: number; p90: number }

/** History line plus forecast fan (P10-P90, P25-P75, P50 line, mean marker). */
export function fanChart(el: HTMLElement, opts: {
  history: { year: number; value: number | null }[];
  forecast: { year: number; label: string; q: Quantiles }[];
  kind: "pct" | "pts" | "money" | "level_pct";
  unit: string;
  yRange?: [number, number];
}) {
  const b = base();
  const c = echarts.init(el);
  live.push(c);
  const years = [...opts.history.map((h) => h.year), ...opts.forecast.map((f) => f.year)];
  const cats = years.map(String);
  const nH = opts.history.length;
  const pad = (arr: (number | null)[], before: number) => [...Array(before).fill(null), ...arr];
  const fmt = (v: number) => opts.kind === "money" ? v.toFixed(1) : opts.kind === "pts" ? `${(v * 100).toFixed(1)} pts` : `${(v * 100).toFixed(1)}%`;
  const axisFmt = (v: number) => opts.kind === "money" ? `${Math.round(v)}` : `${Math.round(v * 1000) / 10}`;
  const fc = opts.forecast;
  // Join the fan to the last history point so the band starts from the actual value.
  const lastH = opts.history[nH - 1]?.value ?? null;
  const join = (vals: number[]) => pad([lastH, ...vals], nH - 1);
  const p10 = fc.map((f) => f.q.p10), p25 = fc.map((f) => f.q.p25), p75 = fc.map((f) => f.q.p75), p90 = fc.map((f) => f.q.p90);
  const accent = css("--chart-1");
  c.setOption({
    textStyle: b.textStyle,
    grid: { ...b.grid, left: 64, right: 24 },
    legend: { ...b.legend, data: ["History", "P50", "Mean", "P10 to P90", "P25 to P75"] },
    tooltip: {
      ...b.tooltip,
      formatter: (ps: { axisValue: string; dataIndex: number }[]) => {
        const i = ps[0].dataIndex;
        if (i < nH) return `<b>${ps[0].axisValue}</b><br>Actual: ${opts.history[i].value === null ? "n/a" : fmt(opts.history[i].value!)}`;
        const f = fc[i - nH];
        return [`<b>${f.year} ${f.label}</b>`, `Mean ${fmt(f.q.mean)}`, `P10 ${fmt(f.q.p10)}`, `P25 ${fmt(f.q.p25)}`,
          `P50 ${fmt(f.q.p50)}`, `P75 ${fmt(f.q.p75)}`, `P90 ${fmt(f.q.p90)}`].join("<br>");
      },
    },
    xAxis: { type: "category", data: cats, boundaryGap: false, ...b.axisCommon, splitLine: { show: false } },
    yAxis: { type: "value", name: opts.unit, scale: true, ...(opts.yRange ? { min: opts.yRange[0], max: opts.yRange[1] } : {}), ...b.axisCommon,
      axisLabel: { color: b.textStyle.color, formatter: axisFmt }, nameTextStyle: { color: b.textStyle.color } },
    series: [
      { name: "History", type: "line", data: opts.history.map((h) => h.value), showSymbol: false, lineStyle: { color: css("--text"), width: 1.6 }, itemStyle: { color: css("--text") } },
      { name: "band-base", type: "line", data: join(p10), stack: "outer", lineStyle: { opacity: 0 }, showSymbol: false, silent: true, tooltip: { show: false } },
      { name: "P10 to P90", type: "line", data: pad([0, ...p90.map((v, i) => v - p10[i])], nH - 1), stack: "outer", lineStyle: { opacity: 0 }, showSymbol: false,
        areaStyle: { color: accent, opacity: 0.15 }, itemStyle: { color: accent }, silent: true },
      { name: "band-base-2", type: "line", data: join(p25), stack: "inner", lineStyle: { opacity: 0 }, showSymbol: false, silent: true, tooltip: { show: false } },
      { name: "P25 to P75", type: "line", data: pad([0, ...p75.map((v, i) => v - p25[i])], nH - 1), stack: "inner", lineStyle: { opacity: 0 }, showSymbol: false,
        areaStyle: { color: accent, opacity: 0.28 }, itemStyle: { color: accent }, silent: true },
      { name: "P50", type: "line", data: join(fc.map((f) => f.q.p50)), showSymbol: false, lineStyle: { color: accent, width: 2 }, itemStyle: { color: accent } },
      { name: "Mean", type: "scatter", data: pad(fc.map((f) => f.q.mean), nH), symbol: "diamond", symbolSize: 8, itemStyle: { color: css("--chart-2") } },
    ],
  });
}

/** Stub year: prior-year months, actual months and simulated months (P10-P90, P50). */
export function stubChart(el: HTMLElement, opts: {
  prior: { period: string; value: number }[];
  actual: { period: string; value: number }[];
  simulated: { period: string; p10: number; p50: number; p90: number }[];
  kind: "pct" | "money"; unit: string;
}) {
  const b = base();
  const c = echarts.init(el);
  live.push(c);
  const cats = [...opts.prior, ...opts.actual].map((x) => x.period).concat(opts.simulated.map((s) => s.period));
  const nP = opts.prior.length, nA = opts.actual.length;
  const fmtAxis = (v: number) => opts.kind === "money" ? `${Math.round(v)}` : `${Math.round(v * 100)}`;
  const empty = (n: number) => Array(n).fill(null);
  const lastA = opts.actual[nA - 1]?.value ?? null;
  const accent = css("--chart-1");
  c.setOption({
    textStyle: b.textStyle,
    grid: { ...b.grid, left: 56, right: 24 },
    legend: { ...b.legend, data: ["Prior year", "Actual", "Simulated P50", "Simulated P10 to P90"] },
    tooltip: { ...b.tooltip, valueFormatter: (v: number) => v === null || v === undefined ? "" : opts.kind === "money" ? v.toFixed(1) : `${(v * 100).toFixed(1)}%` },
    xAxis: { type: "category", data: cats, ...b.axisCommon, splitLine: { show: false } },
    yAxis: { type: "value", name: opts.unit, scale: true, ...b.axisCommon, axisLabel: { color: b.textStyle.color, formatter: fmtAxis }, nameTextStyle: { color: b.textStyle.color } },
    series: [
      { name: "Prior year", type: "line", data: [...opts.prior.map((x) => x.value), ...empty(nA + opts.simulated.length)], showSymbol: false, lineStyle: { color: css("--chart-3"), width: 1.4 }, itemStyle: { color: css("--chart-3") } },
      { name: "Actual", type: "line", data: [...empty(nP), ...opts.actual.map((x) => x.value), ...empty(opts.simulated.length)], symbolSize: 4, lineStyle: { color: css("--text"), width: 1.8 }, itemStyle: { color: css("--text") } },
      { name: "sim-base", type: "line", data: [...empty(nP + nA - 1), lastA, ...opts.simulated.map((s) => s.p10)], stack: "s", lineStyle: { opacity: 0 }, showSymbol: false, silent: true, tooltip: { show: false } },
      { name: "Simulated P10 to P90", type: "line", data: [...empty(nP + nA - 1), 0, ...opts.simulated.map((s) => s.p90 - s.p10)], stack: "s", lineStyle: { opacity: 0 }, showSymbol: false, areaStyle: { color: accent, opacity: 0.2 }, itemStyle: { color: accent }, tooltip: { show: false } },
      { name: "Simulated P50", type: "line", data: [...empty(nP + nA - 1), lastA, ...opts.simulated.map((s) => s.p50)], lineStyle: { color: accent, width: 2, type: "dashed" }, itemStyle: { color: accent }, symbolSize: 4 },
    ],
  });
}
