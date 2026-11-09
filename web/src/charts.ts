import * as echarts from "echarts/core";
import { LineChart } from "echarts/charts";
import { GridComponent, LegendComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";

echarts.use([LineChart, GridComponent, TooltipComponent, LegendComponent, CanvasRenderer]);

const css = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

const live: echarts.ECharts[] = [];
window.addEventListener("resize", () => live.forEach((c) => c.resize()));

export function disposeCharts() {
  while (live.length) live.pop()!.dispose();
}

export interface Band { p10: number; p50: number; p90: number }

/** History line, the ledger forecast line and its P10 to P90 band. */
export function ledgerChart(el: HTMLElement, opts: {
  history: { year: number; value: number }[];
  forecast: { year: number; label: string; value: number; band: Band }[];
  fmt: (v: number) => string;
  unit: string;
}) {
  const c = echarts.init(el);
  live.push(c);
  const text = css("--muted"), line = css("--line"), accent = css("--chart-1");
  const nH = opts.history.length;
  const cats = [...opts.history.map((h) => String(h.year)), ...opts.forecast.map((f) => String(f.year))];
  const pad = (arr: (number | null)[], n: number) => [...Array(n).fill(null), ...arr];
  const lastH = opts.history[nH - 1]?.value ?? null;
  const lo = opts.forecast.map((f) => f.band.p10), hi = opts.forecast.map((f) => f.band.p90);
  c.setOption({
    textStyle: { fontFamily: "inherit", color: text },
    grid: { left: 64, right: 24, top: 40, bottom: 32 },
    legend: { top: 0, data: ["Actual", "Forecast", "P10 to P90"], textStyle: { color: text }, itemWidth: 14, itemHeight: 8 },
    tooltip: {
      trigger: "axis", backgroundColor: css("--surface"), borderColor: css("--line-strong"), textStyle: { color: css("--text"), fontSize: 12 },
      formatter: (ps: { axisValue: string; dataIndex: number }[]) => {
        const i = ps[0].dataIndex;
        if (i < nH) return `<b>${ps[0].axisValue}</b><br>Actual ${opts.fmt(opts.history[i].value)}`;
        const f = opts.forecast[i - nH];
        return `<b>${f.label}</b><br>Forecast ${opts.fmt(f.value)}<br>P10 ${opts.fmt(f.band.p10)}, P90 ${opts.fmt(f.band.p90)}`;
      },
    },
    xAxis: { type: "category", data: cats, boundaryGap: false, axisLine: { lineStyle: { color: line } }, axisLabel: { color: text } },
    yAxis: { type: "value", name: opts.unit, scale: true, splitLine: { lineStyle: { color: line } }, axisLabel: { color: text, formatter: (v: number) => opts.fmt(v) },
      nameTextStyle: { color: text } },
    series: [
      { name: "Actual", type: "line", data: opts.history.map((h) => h.value), showSymbol: false, lineStyle: { color: css("--text"), width: 1.6 }, itemStyle: { color: css("--text") } },
      { name: "base", type: "line", data: pad([lastH, ...lo], nH - 1), stack: "band", lineStyle: { opacity: 0 }, showSymbol: false, silent: true, tooltip: { show: false } },
      { name: "P10 to P90", type: "line", data: pad([0, ...hi.map((v, i) => v - lo[i])], nH - 1), stack: "band", lineStyle: { opacity: 0 }, showSymbol: false,
        areaStyle: { color: accent, opacity: 0.18 }, itemStyle: { color: accent }, silent: true },
      { name: "Forecast", type: "line", data: pad([lastH, ...opts.forecast.map((f) => f.value)], nH - 1), symbolSize: 5, lineStyle: { color: accent, width: 2 }, itemStyle: { color: accent } },
    ],
  });
}
