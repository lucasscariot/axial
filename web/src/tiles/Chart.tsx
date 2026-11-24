import { useEffect, useRef, useState } from "react";
import * as echarts from "echarts/core";
import { BarChart, LineChart, ScatterChart } from "echarts/charts";
import { GridComponent, LegendComponent, TooltipComponent } from "echarts/components";
import { SVGRenderer } from "echarts/renderers";
import type { QueryMeta, Row, Tile } from "../api";
import { defaultFormat, fmt, labelOf } from "../format";

echarts.use([LineChart, BarChart, ScatterChart, GridComponent, TooltipComponent, LegendComponent, SVGRenderer]);

export const MAX_SERIES = 8;

export function cssVar(el: Element, name: string) {
  return getComputedStyle(el).getPropertyValue(name).trim();
}

export function useThemeChange(cb: () => void) {
  useEffect(() => {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    mq.addEventListener("change", cb);
    window.addEventListener("axial:theme", cb);
    return () => { mq.removeEventListener("change", cb); window.removeEventListener("axial:theme", cb); };
  }, [cb]);
}

// ECharts caches text widths per font. Measuring before Geist loads caches fallback widths
// and clips axis labels, so charts wait for the font (or give up after 1.5 s).
export const fontsReady: Promise<unknown> = Promise.race([
  Promise.all([
    document.fonts?.load('400 11px "Geist Variable"'),
    document.fonts?.load('400 12px "Geist Variable"'),
  ]),
  new Promise((r) => setTimeout(r, 1500)),
]);

export const esc = (s: string) =>
  s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

export function withAlpha(hex: string, a: number) {
  const n = parseInt(hex.replace("#", ""), 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${a})`;
}

const DURATION_STEPS = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200, 86400];

/** A y scale from 0 (or below) with about four round steps. Durations (seconds) step by
 *  natural units: 1–30 s, 1–30 min, 1–12 h, then whole days; counts by whole numbers. */
export function niceScale(lo: number, hi: number, format: string) {
  lo = Math.min(0, lo); hi = Math.max(0, hi);
  if (!(hi > lo)) hi = lo + 1;
  const raw = (hi - lo) / 4, mag = 10 ** Math.floor(Math.log10(raw)), f = raw / mag;
  let step: number;
  if (format === "duration") {
    const days = raw / 86400, dmag = 10 ** Math.max(0, Math.floor(Math.log10(days))), df = days / dmag;
    step = DURATION_STEPS.find((d) => d >= raw) ?? (df <= 1 ? 1 : df <= 2 ? 2 : df <= 5 ? 5 : 10) * dmag * 86400;
  } else {
    const whole = format === "integer"; // counts step by whole numbers, never 2.5
    step = (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 && !whole ? 2.5 : f <= 5 ? 5 : 10) * mag;
    if (whole && step < 1) step = 1;
  }
  const round = (v: number) => Number(v.toPrecision(12));
  return { min: round(Math.floor(lo / step + 1e-9) * step), max: round(Math.ceil(hi / step - 1e-9) * step), step };
}

/** Which values a line cannot connect to anything: no non-null value right before or after. */
export function isolated(values: (number | null)[]) {
  return values.map((v, i) => v !== null && (values[i - 1] ?? null) === null && (values[i + 1] ?? null) === null);
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
  const [hidden, setHidden] = useState<Record<string, boolean>>({});
  const { xs, series, yCol } = buildSeries(tile, rows);
  const names = series.map((s) => (tile.series ? s.name : labelOf(query, s.name)));
  const multi = series.length > 1;
  const isBar = tile.type === "bar";

  const render = () => {
    const el = ref.current;
    if (!el) return;
    // SVG keeps charts sharp and their text selectable in exported PDFs
    if (!chart.current) chart.current = echarts.init(el, undefined, { renderer: "svg" });
    const xFmt = defaultFormat(query.columns[tile.x!]);
    const yFmt = tile.format ?? query.formats[yCol] ?? defaultFormat(query.columns[yCol]);
    const colors = Array.from({ length: 8 }, (_, i) => cssVar(el, `--series-${i + 1}`));
    const text1 = cssVar(el, "--text-1");
    const text2 = cssVar(el, "--text-2");
    const text3 = cssVar(el, "--text-3");
    const grid = cssVar(el, "--grid");
    const surface = cssVar(el, "--card");
    const border = cssVar(el, "--border");
    const font = cssVar(el, "--font-sans");
    const few = xs.length <= 2;
    // a duration axis steps by whole seconds, minutes, hours or days, never 3m 20s
    let durationScale = {};
    if (yFmt === "duration") {
      const shown = series.filter((s) => !multi || hidden[s.name] !== true).flatMap((s) => s.data).filter((v): v is number => v !== null);
      if (shown.length) { const sc = niceScale(Math.min(...shown), Math.max(...shown), "duration"); durationScale = { min: sc.min, max: sc.max, interval: sc.step }; }
    }

    const tooltipHtml = (params: any) => {
      const list = (Array.isArray(params) ? params : [params]).filter((p: any) => p.seriesType !== "scatter");
      if (!list.length) return "";
      const head = `<div class="tt-head">${esc(fmt(xs[list[0].dataIndex], xFmt))}</div>`;
      const rowsHtml = list.map((p: any) =>
        `<div class="tt-row"><i class="tt-key ${isBar ? "bar" : "line"}" style="background:${p.color}"></i>` +
        `<b>${esc(fmt(p.value, yFmt))}</b><span>${esc(names[p.seriesIndex] ?? "")}</span></div>`).join("");
      return head + rowsHtml;
    };

    chart.current.setOption({
      color: colors,
      animation: false,
      textStyle: { fontFamily: font, fontSize: 11, color: text2 },
      grid: { left: 8, right: isBar || few ? 8 : 20, top: 8, bottom: 0, containLabel: true },
      // The wrapping legend lives in normal document flow above the canvas.
      // Keep ECharts' selection model so hidden series also leave the axes and tooltip.
      legend: { show: false, selected: Object.fromEntries(series.map((s) => [s.name, !multi || hidden[s.name] !== true])) },
      tooltip: {
        trigger: "axis",
        className: "chart-tooltip",
        axisPointer: isBar
          ? { type: "shadow", shadowStyle: { color: withAlpha(text1.startsWith("#") ? text1 : "#000000", 0.04) } }
          : { type: "line", lineStyle: { color: text3, width: 1, type: "solid" } },
        backgroundColor: surface, borderColor: border, borderWidth: 1, padding: [8, 10],
        textStyle: { color: text1, fontSize: 12, fontFamily: font },
        extraCssText: "border-radius:8px;box-shadow:0 4px 16px rgba(0,0,0,.08);",
        formatter: tooltipHtml,
      },
      xAxis: {
        type: "category", data: xs, boundaryGap: isBar || few,
        axisLine: { lineStyle: { color: border } }, axisTick: { show: false },
        axisLabel: { color: text3, fontFamily: font, fontSize: 11, margin: 10, hideOverlap: true, formatter: (v: string) => fmt(v, xFmt, true) },
      },
      yAxis: {
        type: "value", splitNumber: 4, ...durationScale,
        splitLine: { lineStyle: { color: grid, width: 1 } },
        axisLabel: { color: text3, fontFamily: font, fontSize: 11, margin: 10, formatter: (v: number) => fmt(v, yFmt, true) },
      },
      series: [...series.map((s, i) => isBar
        ? { type: "bar", name: s.name, data: s.data, barMaxWidth: 24, barGap: "12%", barCategoryGap: "32%",
            itemStyle: { borderRadius: [4, 4, 0, 0] }, emphasis: { itemStyle: { opacity: 0.85 } } }
        : { type: "line", name: s.name, data: s.data, connectNulls: false,
            showSymbol: few, symbol: "circle", symbolSize: 10,
            itemStyle: { color: colors[i], borderColor: surface, borderWidth: 2 },
            lineStyle: { width: 2, cap: "round", join: "round" },
            emphasis: { scale: false, focus: multi ? "series" : "none" },
            ...(multi ? {} : {
              areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                { offset: 0, color: withAlpha(colors[i], 0.12) }, { offset: 1, color: withAlpha(colors[i], 0) }]) },
            }) }), ...(isBar || few ? [] : series.map((s, i) => {
              // A value with no neighbour to connect to draws no line; give it a dot so it is not lost.
              const lone = isolated(s.data);
              return { type: "scatter", name: s.name, data: s.data.map((v, j) => (lone[j] ? v : null)), silent: true, z: 3,
                symbolSize: 6, itemStyle: { color: colors[i] } };
            }))],
    }, true);
  };

  const [fontOk, setFontOk] = useState(false);
  useEffect(() => { let live = true; fontsReady.then(() => live && setFontOk(true)); return () => { live = false; }; }, []);
  useEffect(() => { if (fontOk) render(); }, [fontOk, tile, query, rows, hidden]);
  useThemeChange(() => { if (fontOk) render(); });
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(() => chart.current?.resize());
    ro.observe(el);
    return () => { ro.disconnect(); chart.current?.dispose(); chart.current = null; };
  }, []);

  if (rows.length === 0) return <div className="tile-empty">No rows for this range</div>;
  return (
    <div className="chart" style={{ height }}>
      {multi && (
        <div className="chart-legend" role="group" aria-label="Chart legend">
          {series.map((s, i) => (
            <button key={s.name} type="button" className="chart-legend-item" aria-pressed={hidden[s.name] !== true}
              onClick={() => setHidden((prev) => ({ ...prev, [s.name]: prev[s.name] !== true }))}
              onMouseEnter={() => chart.current?.dispatchAction({ type: "highlight", seriesName: s.name })}
              onMouseLeave={() => chart.current?.dispatchAction({ type: "downplay", seriesName: s.name })}>
              <i className={`chart-legend-key${isBar ? " bar" : ""}`} aria-hidden
                style={{ background: hidden[s.name] === true ? "var(--text-3)" : `var(--series-${i + 1})` }} />
              <span>{names[i]}</span>
            </button>
          ))}
        </div>
      )}
      <div ref={ref} className="chart-plot" role="img" aria-label={`${tile.type} chart of ${String(tile.y)} by ${tile.x}`} />
    </div>
  );
}
