import { useEffect, useRef } from "react";
import * as echarts from "echarts/core";
import { BarChart, LineChart } from "echarts/charts";
import { GridComponent, LegendComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import type { QueryMeta, Row, Tile } from "../api";
import { defaultFormat, fmt } from "../format";

echarts.use([LineChart, BarChart, GridComponent, TooltipComponent, LegendComponent, CanvasRenderer]);

const MAX_SERIES = 8;

function cssVar(el: Element, name: string) {
  return getComputedStyle(el).getPropertyValue(name).trim();
}

function useColorScheme(cb: () => void) {
  useEffect(() => {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    mq.addEventListener("change", cb);
    return () => mq.removeEventListener("change", cb);
  }, [cb]);
}

export function buildSeries(tile: Tile, rows: Row[]) {
  const x = tile.x!;
  const xs: unknown[] = [];
  const seen = new Set<string>();
  for (const r of rows) { const k = String(r[x]); if (!seen.has(k)) { seen.add(k); xs.push(r[x]); } }
  const idx = new Map(xs.map((v, i) => [String(v), i]));
  if (tile.series) {
    const y = Array.isArray(tile.y) ? tile.y[0] : tile.y!;
    const names: string[] = [];
    const data = new Map<string, (number | null)[]>();
    for (const r of rows) {
      const s = String(r[tile.series] ?? "∅");
      if (!data.has(s)) { names.push(s); data.set(s, new Array(xs.length).fill(null)); }
      data.get(s)![idx.get(String(r[x]))!] = r[y] == null ? null : Number(r[y]);
    }
    // Keep the largest series, fold the rest into "Other" (never generate a 9th hue)
    const total = (s: string) => data.get(s)!.reduce<number>((a, b) => a + (b ?? 0), 0);
    const ordered = [...names].sort((a, b) => total(b) - total(a));
    const keep = ordered.length > MAX_SERIES ? ordered.slice(0, MAX_SERIES - 1) : ordered;
    const out = names.filter((n) => keep.includes(n)).map((n) => ({ name: n, data: data.get(n)! }));
    if (ordered.length > MAX_SERIES) {
      const other = new Array(xs.length).fill(null) as (number | null)[];
      for (const n of ordered.slice(MAX_SERIES - 1)) data.get(n)!.forEach((v, i) => { if (v !== null) other[i] = (other[i] ?? 0) + v; });
      out.push({ name: "Other", data: other });
    }
    return { xs, series: out, yCol: y };
  }
  const ys = Array.isArray(tile.y) ? tile.y : [tile.y!];
  return { xs, series: ys.slice(0, MAX_SERIES).map((c) => ({ name: c, data: rows.map((r) => (r[c] == null ? null : Number(r[c]))) })), yCol: ys[0] };
}

export function ChartTile({ tile, query, rows, height }: { tile: Tile; query: QueryMeta; rows: Row[]; height: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.ECharts | null>(null);

  const render = () => {
    const el = ref.current;
    if (!el) return;
    if (!chart.current) chart.current = echarts.init(el, undefined, { renderer: "canvas" });
    const { xs, series, yCol } = buildSeries(tile, rows);
    const xFmt = defaultFormat(query.columns[tile.x!]);
    const yFmt = tile.format ?? query.formats[yCol] ?? defaultFormat(query.columns[yCol]);
    const colors = Array.from({ length: 8 }, (_, i) => cssVar(el, `--series-${i + 1}`));
    const text2 = cssVar(el, "--text-secondary");
    const muted = cssVar(el, "--text-muted");
    const grid = cssVar(el, "--grid");
    const surface = cssVar(el, "--surface-1");
    const multi = series.length > 1;
    const isBar = tile.type === "bar";
    chart.current.setOption({
      color: colors,
      animation: false,
      textStyle: { fontFamily: "inherit", fontSize: 11, color: text2 },
      grid: { left: 4, right: 12, top: multi ? 24 : 8, bottom: 2, containLabel: true },
      legend: multi ? { top: 0, left: 0, itemWidth: 10, itemHeight: 10, icon: "roundRect", textStyle: { color: text2, fontSize: 11 } } : { show: false },
      tooltip: {
        trigger: "axis",
        axisPointer: { type: isBar ? "shadow" : "line", lineStyle: { color: muted } },
        backgroundColor: surface, borderColor: grid, textStyle: { color: cssVar(el, "--text-primary"), fontSize: 12 },
        valueFormatter: (v: unknown) => fmt(v, yFmt),
      },
      xAxis: {
        type: "category", data: xs, boundaryGap: isBar,
        axisLine: { lineStyle: { color: grid } }, axisTick: { show: false },
        axisLabel: { color: muted, hideOverlap: true, alignMinLabel: isBar ? undefined : "left", alignMaxLabel: isBar ? undefined : "right", formatter: (v: string) => fmt(v, xFmt, true) },
      },
      yAxis: {
        type: "value", splitNumber: 4,
        splitLine: { lineStyle: { color: grid } },
        axisLabel: { color: muted, formatter: (v: number) => fmt(v, yFmt, true) },
      },
      series: series.map((s) => isBar
        ? { type: "bar", name: s.name, data: s.data, barMaxWidth: 28, barGap: "10%", itemStyle: { borderRadius: [4, 4, 0, 0] }, emphasis: { focus: "series" } }
        : { type: "line", name: s.name, data: s.data, showSymbol: xs.length <= 2, symbolSize: 8, lineStyle: { width: 2 }, connectNulls: false, emphasis: { focus: "series" } }),
    }, true);
  };

  useEffect(render, [tile, query, rows]);
  useColorScheme(render);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(() => chart.current?.resize());
    ro.observe(el);
    return () => { ro.disconnect(); chart.current?.dispose(); chart.current = null; };
  }, []);

  if (rows.length === 0) return <div className="tile-msg muted">No rows for this range</div>;
  return <div ref={ref} style={{ width: "100%", height }} role="img" aria-label={`${tile.type} chart of ${String(tile.y)} by ${tile.x}`} />;
}
