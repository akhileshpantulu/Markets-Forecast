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
