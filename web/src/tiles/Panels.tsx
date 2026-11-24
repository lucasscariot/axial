// Line and bar tiles with a time axis, small multiples (`facet`), `lanes`, shaded periods
// (`highlight`) or `step` lines. Each facet panel is its own ECharts instance (its lanes are grids
// in that instance), so panels are plain DOM: CSS lays them out in columns, print breaks pages
// between them, and the crosshair crosses every panel through one CSS variable. Panels share the
// x extent, every y scale and the left gutter, so the same pixel is the same instant everywhere.
import { useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import * as echarts from "echarts/core";
import { BarChart, CustomChart, LineChart, ScatterChart } from "echarts/charts";
import { GraphicComponent, GridComponent, LegendComponent, TooltipComponent } from "echarts/components";
import { SVGRenderer } from "echarts/renderers";
import type { QueryMeta, Row, Tile } from "../api";
import { defaultFormat, fmt, fmtTime, labelOf, toMs } from "../format";
import { MAX_SERIES, cssVar, esc, fontsReady, isolated, niceScale, useThemeChange, withAlpha } from "./Chart";

echarts.use([LineChart, BarChart, CustomChart, ScatterChart, GridComponent, GraphicComponent, TooltipComponent, LegendComponent, SVGRenderer]);

export const MAX_FACETS = 50;
const TIME = new Set(["TIMESTAMP", "DATETIME"]);
const TOP = 6, AXIS_H = 24, GAP = 12, LABEL_H = 16, RIGHT = 18;

/** Whether a line or bar tile needs this renderer rather than the plain category chart. */
export function usesPanels(tile: Tile, query: QueryMeta) {
  return !!(tile.facet || tile.lanes || tile.highlight || tile.step) || TIME.has(query.columns[tile.x ?? ""]);
}

/** Per-panel chart height of a faceted tile, or the whole chart height of a lanes tile. */
export function panelHeight(tile: Tile) {
  const lanes = tile.lanes && Array.isArray(tile.y) ? Math.min(tile.y.length, MAX_SERIES) : 1;
  if (tile.height) return tile.height;
  if (tile.facet) return lanes > 1 ? 100 * lanes : 140;
  return Math.max(220, 110 * lanes);
}

type Line = { key: string; name: string; col: string; lane: number; color: number; step: boolean; format: string };
type Point = number | null;
type Panel = { key: string; title: string; data: Record<string, Point[] | [number, Point][]>; runs: [number, number][] };
type Model = {
  time: boolean; civil: boolean; xs: unknown[]; xFmt: string; xMin: number; xMax: number; few: boolean;
  lines: Line[]; laneCount: number; lanes: boolean; panels: Panel[]; total: number; highlight: string | null; highlightLabel: string;
};

const num = (v: unknown): Point => { if (v === null || v === undefined) return null; const n = Number(v); return Number.isFinite(n) ? n : null; };

function buildModel(tile: Tile, query: QueryMeta, rows: Row[], range?: { start: string; end: string }): Model {
  const x = tile.x!, xType = query.columns[x];
  const time = TIME.has(xType), civil = xType === "DATETIME";
  const ys = (Array.isArray(tile.y) ? tile.y : [tile.y!]).slice(0, MAX_SERIES);
  const lanes = !!tile.lanes && ys.length > 1 && !tile.series;
  const steps = new Set(tile.type !== "line" ? [] : tile.step === true ? ys : Array.isArray(tile.step) ? tile.step : typeof tile.step === "string" ? [tile.step] : []);
  const fmtOf = (c: string) => tile.format ?? query.formats[c] ?? defaultFormat(query.columns[c]);

  // x positions: instants on a time axis, else indexes of the distinct values in arrival order
  const xs: unknown[] = [];
  const idx = new Map<string, number>();
  if (!time) for (const r of rows) { const k = String(r[x]); if (!idx.has(k)) { idx.set(k, xs.length); xs.push(r[x]); } }
  const xOf = (r: Row) => (time ? toMs(r[x], civil) : idx.get(String(r[x])) ?? null);

  // series split: the 7 largest values over the whole tile keep a colour, the rest fold into "Other"
  let lines: Line[];
  let lineOf: (r: Row) => string = () => "";
  if (tile.series) {
    const s = tile.series, y = ys[0];
    const totals = new Map<string, number>();
    for (const r of rows) { const k = String(r[s] ?? "∅"); totals.set(k, (totals.get(k) ?? 0) + (num(r[y]) ?? 0)); }
    const names = [...totals.keys()];
    const ordered = [...names].sort((a, b) => totals.get(b)! - totals.get(a)!);
    const keep = new Set(ordered.length > MAX_SERIES ? ordered.slice(0, MAX_SERIES - 1) : ordered);
    const kept = names.filter((n) => keep.has(n));
    if (ordered.length > MAX_SERIES) kept.push("Other");
    lines = kept.map((n, i) => ({ key: n, name: n, col: y, lane: 0, color: i, step: steps.has(y), format: fmtOf(y) }));
    lineOf = (r) => { const k = String(r[s] ?? "∅"); return keep.has(k) ? k : "Other"; };
  } else {
    lines = ys.map((c, i) => ({ key: c, name: labelOf(query, c), col: c, lane: lanes ? i : 0, color: i, step: steps.has(c), format: fmtOf(c) }));
  }

  // one panel per facet value, in the order the rows arrive
  const groups = new Map<string, { value: unknown; rows: Row[] }>();
  for (const r of rows) {
    const k = tile.facet ? String(r[tile.facet] ?? "∅") : "";
    let g = groups.get(k);
    if (!g) groups.set(k, g = { value: tile.facet ? r[tile.facet] : null, rows: [] });
    g.rows.push(r);
  }
  const facetFmt = tile.facet ? query.formats[tile.facet] ?? defaultFormat(query.columns[tile.facet]) : "text";
  let lo = Infinity, hi = -Infinity, most = 0;
  const panels: Panel[] = [...groups].slice(0, MAX_FACETS).map(([key, g]) => {
    const maps = new Map(lines.map((l) => [l.key, new Map<number, Point>()]));
    const flags = new Map<number, boolean>();
    for (const r of g.rows) {
      const at = xOf(r);
      if (at === null) continue;
      if (tile.series) {
        const k = lineOf(r), m = maps.get(k)!, v = num(r[ys[0]]);
        m.set(at, k === "Other" && m.get(at) != null ? (m.get(at) ?? 0) + (v ?? 0) : v);
      } else for (const l of lines) maps.get(l.key)!.set(at, num(r[l.col]));
      if (tile.highlight) flags.set(at, flags.get(at) === true || r[tile.highlight] === true);
      if (time) { lo = Math.min(lo, at); hi = Math.max(hi, at); }
    }
    const data: Panel["data"] = {};
    for (const [k, m] of maps) {
      most = Math.max(most, m.size);
      if (time) data[k] = [...m].sort((a, b) => a[0] - b[0]);
      else { const arr: Point[] = new Array(xs.length).fill(null); for (const [i, v] of m) arr[i] = v; data[k] = arr; }
    }
    // contiguous runs of points where the highlight column is true, from first to last point
    const runs: [number, number][] = [];
    let open: [number, number] | null = null;
    for (const at of [...flags.keys()].sort((a, b) => a - b)) {
      if (flags.get(at)) { if (open) open[1] = at; else runs.push(open = [at, at]); } else open = null;
    }
    return { key, title: tile.facet ? fmt(g.value, facetFmt) : "", data, runs };
  });

  // the selected date range, from the start of its first day to the end of its last, or the data
  let xMin = lo, xMax = hi;
  if (time && range?.start && range.end) {
    const day = (iso: string) => (civil ? toMs(iso, true)! : new Date(iso + "T00:00:00").getTime());
    xMin = Math.min(day(range.start), lo); xMax = Math.max(day(range.end), hi);
  }
  if (time && xMin === xMax) { xMin -= 1800e3; xMax += 1800e3; }
  return {
    time, civil, xs, xFmt: defaultFormat(xType), xMin, xMax, few: time ? most <= 2 : xs.length <= 2,
    lines, laneCount: lanes ? lines.length : 1, lanes, panels, total: groups.size, highlight: tile.highlight ?? null,
    highlightLabel: tile.highlight ? labelOf(query, tile.highlight) : "",
  };
}

type Scale = { min: number; max: number; step: number; format: string };

/** One y scale per lane over every panel, so panels compare at a glance. */
function laneScales(m: Model, hidden: Record<string, boolean>): Scale[] {
  if (m.lines.length === 0) return []; // a series split over no rows has no lines, and nothing to draw
  return Array.from({ length: m.laneCount }, (_, lane) => {
    let lo = Infinity, hi = -Infinity;
    const lines = m.lines.filter((l) => l.lane === lane);
    for (const l of lines) {
      if (hidden[l.key] && lines.length > 1) continue;
      for (const p of m.panels) for (const d of p.data[l.key]) {
        const v = Array.isArray(d) ? d[1] : d;
        if (v !== null) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
      }
    }
    const format = lines[0].format;
    return { ...niceScale(lo === Infinity ? 0 : lo, hi === -Infinity ? 1 : hi, format), format };
  });
}

let measureCtx: CanvasRenderingContext2D | null = null;
/** The widest y tick label of any lane, so every lane and panel starts its plot at the same x. */
function gutter(scales: Scale[], font: string) {
  measureCtx ??= document.createElement("canvas").getContext("2d");
  if (!measureCtx) return 48;
  measureCtx.font = `11px ${font}`;
  let w = 0;
  for (const s of scales) for (let v = s.min; v <= s.max + s.step / 2; v += s.step) w = Math.max(w, measureCtx.measureText(fmt(v, s.format, true)).width);
  return Math.ceil(w) + 12;
}

function geometry(height: number, m: Model) {
  const lh = m.lanes ? LABEL_H : 0;
  const plot = Math.max(24, (height - TOP - AXIS_H - (m.laneCount - 1) * GAP - m.laneCount * lh) / m.laneCount);
  return Array.from({ length: m.laneCount }, (_, i) => ({ top: TOP + lh + i * (plot + GAP + lh), height: plot }));
}

type Props = { tile: Tile; query: QueryMeta; rows: Row[]; height: number; range?: { start: string; end: string } };

export function PanelsChart({ tile, query, rows, height, range }: Props) {
  const model = useMemo(() => buildModel(tile, query, rows, range), [tile, query, rows, range?.start, range?.end]);
  const [hidden, setHidden] = useState<Record<string, boolean>>({});
  const [shade, setShade] = useState(true);
  const scales = useMemo(() => laneScales(model, hidden), [model, hidden]);
  const host = useRef<HTMLDivElement>(null);
  const charts = useRef(new Set<echarts.ECharts>());
  const [fontOk, setFontOk] = useState(false);
  useEffect(() => { let live = true; fontsReady.then(() => live && setFontOk(true)); return () => { live = false; }; }, []);

  if (rows.length === 0) return <div className="tile-empty">No rows for this range</div>;
  const isBar = tile.type === "bar";
  const legendLines = model.lanes || model.lines.length < 2 ? [] : model.lines;
  const faceted = !!tile.facet;
  // fewer panels than columns share the full width rather than leaving empty columns
  const cols = faceted ? Math.min(4, Math.max(1, tile.facet_columns ?? 1), model.panels.length) : 1;
  const each = (fn: (c: echarts.ECharts) => void) => charts.current.forEach(fn);
  return (
    <div className={`chart${faceted ? " chart-faceted" : ""}`} style={faceted ? undefined : { height }}>
      {(legendLines.length > 0 || model.highlight) && (
        <div className="chart-legend" role="group" aria-label="Chart legend">
          {legendLines.map((l) => (
            <button key={l.key} type="button" className="chart-legend-item" aria-pressed={hidden[l.key] !== true}
              onClick={() => setHidden((prev) => ({ ...prev, [l.key]: prev[l.key] !== true }))}
              onMouseEnter={() => each((c) => c.dispatchAction({ type: "highlight", seriesName: l.key }))}
              onMouseLeave={() => each((c) => c.dispatchAction({ type: "downplay", seriesName: l.key }))}>
              <i className={`chart-legend-key${isBar ? " bar" : ""}`} aria-hidden
                style={{ background: hidden[l.key] === true ? "var(--text-3)" : `var(--series-${l.color + 1})` }} />
              <span>{l.name}</span>
            </button>
          ))}
          {model.highlight && (
            <button type="button" className="chart-legend-item" aria-pressed={shade} onClick={() => setShade((s) => !s)}
              title="Shaded where this is true">
              <i className={`chart-legend-key shade${shade ? "" : " off"}`} aria-hidden />
              <span>{model.highlightLabel}</span>
            </button>
          )}
        </div>
      )}
      {model.total > model.panels.length && (
        <p className="facet-note" role="status">
          Showing {model.panels.length} of {model.total} {labelOf(query, tile.facet!).toLowerCase()} panels
        </p>
      )}
      <div ref={host} className={faceted ? "facets" : "facets single"} style={faceted ? { "--facet-cols": cols } as CSSProperties : undefined}>
        {model.panels.map((p) => (
          <PanelChart key={p.key} tile={tile} model={model} panel={p} scales={scales} hidden={hidden} shade={shade}
            height={faceted ? height : undefined} host={host} charts={charts.current} fontOk={fontOk} />
        ))}
      </div>
    </div>
  );
}

type PanelProps = {
  tile: Tile; model: Model; panel: Panel; scales: Scale[]; hidden: Record<string, boolean>; shade: boolean;
  height?: number; host: React.RefObject<HTMLDivElement>; charts: Set<echarts.ECharts>; fontOk: boolean;
};

function PanelChart({ tile, model: m, panel, scales, hidden, shade, height, host, charts, fontOk }: PanelProps) {
  const ref = useRef<HTMLDivElement>(null);
  const cross = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.ECharts | null>(null);
  const isBar = tile.type === "bar";

  const render = () => {
    const el = ref.current;
    if (!el || !el.clientHeight) return;
    if (!chart.current) {
      // SVG keeps charts sharp and their text selectable in exported PDFs
      const c = chart.current = echarts.init(el, undefined, { renderer: "svg" });
      charts.add(c);
      if (host.current && host.current.children.length > 1) {
        // The hovered panel draws its own crosshair; the others show one at the same x.
        c.on("updateAxisPointer", (e: any) => {
          const info = e.axesInfo?.[0], h = host.current;
          if (!h || !info) return;
          h.querySelector(".facet-panel.hover")?.classList.remove("hover");
          el.closest(".facet-panel")?.classList.add("hover");
          h.style.setProperty("--cross-x", `${c.convertToPixel({ xAxisIndex: 0 } as any, info.value)}px`);
          h.classList.add("crossing");
        });
        c.getZr().on("globalout", () => host.current?.classList.remove("crossing"));
      }
    }
    const colors = Array.from({ length: 8 }, (_, i) => cssVar(el, `--series-${i + 1}`));
    const [text1, text2, text3, grid, surface, border, font] =
      ["--text-1", "--text-2", "--text-3", "--grid", "--card", "--border", "--font-sans"].map((v) => cssVar(el, v));
    const wash = cssVar(el, "--highlight-bg"), edge = cssVar(el, "--highlight-edge");
    const left = gutter(scales, font);
    const lanes = geometry(el.clientHeight, m);
    const last = lanes[lanes.length - 1];
    if (cross.current) Object.assign(cross.current.style, { top: `${lanes[0].top}px`, height: `${last.top + last.height - lanes[0].top}px` });
    const right = m.time || !(isBar || m.few) ? RIGHT : 8;
    const n = m.xs.length, band = isBar || m.few;
    const ticks = Math.max(2, Math.floor((el.clientWidth - left - right) / 80)); // a time label needs ~80px
    const xAxisFmt = (v: any) => (m.time ? fmtTime(Number(v), m.civil, "axis") : fmt(v, m.xFmt, true));
    const label = (v: unknown) => (m.time ? fmtTime(Number(v), m.civil, "full") : fmt(v, m.xFmt));
    const inRun = (at: number) => panel.runs.some(([a, b]) => at >= a && at <= b);

    const tooltipHtml = (params: any) => {
      const list = (Array.isArray(params) ? params : [params]).filter((p: any) => p.seriesType === "line" || p.seriesType === "bar");
      if (!list.length) return "";
      const p0 = list[0];
      const at = m.time ? Number(p0.value[0]) : p0.dataIndex;
      const head = `<div class="tt-head">${panel.title ? `${esc(panel.title)} · ` : ""}${esc(label(m.time ? at : m.xs[at]))}</div>`;
      const body = list.map((p: any) => {
        const l = m.lines[p.seriesIndex];
        const v = m.time ? p.value[1] : p.value;
        return `<div class="tt-row"><i class="tt-key ${isBar ? "bar" : "line"}" style="background:${p.color}"></i>` +
          `<b>${esc(fmt(v, l.format))}</b><span>${esc(l.name)}</span></div>`;
      }).join("");
      const run = m.highlight && shade && inRun(at)
        ? `<div class="tt-row"><i class="tt-key shade"></i><span>${esc(m.highlightLabel)}</span></div>` : "";
      return head + body + run;
    };

    // Shaded runs: drawn without a coordinate system so they never take part in the tooltip or
    // in snapping. Positions follow the axes exactly because the extents and gutter are ours.
    const toPx = (v: number, width: number) => {
      const w = width - left - right;
      if (m.time) return left + ((v - m.xMin) / (m.xMax - m.xMin)) * w;
      return band ? left + (v + 0.5) * (w / n) : left + (n > 1 ? v * (w / (n - 1)) : w / 2);
    };
    const highlightSeries = m.highlight && shade && panel.runs.length ? [{
      type: "custom", coordinateSystem: "none", silent: true, z: 1, data: panel.runs,
      renderItem: (_: any, api: any) => {
        const width = api.getWidth();
        let a = toPx(api.value(0), width), b = toPx(api.value(1), width);
        if (!m.time) { const half = (width - left - right) / (band ? n : Math.max(1, n - 1)) / 2; a -= half; b += half; }
        if (b - a < 4) { const mid = (a + b) / 2; a = mid - 2; b = mid + 2; } // a run of one reading stays visible
        a = Math.max(a, left); b = Math.min(b, width - right);
        if (b <= a) return null;
        return { type: "group", children: lanes.flatMap((g) => [
          { type: "rect", shape: { x: a, y: g.top, width: b - a, height: g.height }, style: { fill: wash } },
          // thin edges at the run's start and end, never on the baseline where a zero reading lies
          { type: "rect", shape: { x: a, y: g.top, width: 1, height: g.height }, style: { fill: edge } },
          { type: "rect", shape: { x: b - 1, y: g.top, width: 1, height: g.height }, style: { fill: edge } },
        ]) };
      },
    }] : [];

    const perLane = m.lines.reduce<number[]>((acc, l) => { acc[l.lane] = (acc[l.lane] ?? 0) + 1; return acc; }, []);
    chart.current.setOption({
      color: colors, animation: false, useUTC: m.civil,
      textStyle: { fontFamily: font, fontSize: 11, color: text2 },
      grid: lanes.map((g) => ({ left, right, top: g.top, height: g.height, containLabel: false })),
      legend: { show: false, selected: Object.fromEntries(m.lines.map((l) => [l.key, m.lanes || m.lines.length < 2 || hidden[l.key] !== true])) },
      axisPointer: { link: [{ xAxisIndex: "all" }] },
      graphic: m.lanes ? m.lines.map((l, i) => ({
        type: "text", left, top: lanes[i].top - LABEL_H + 1, silent: true, // beside the gutter, clear of tick labels
        style: { text: l.name, fill: text2, font: `500 11px ${font}` },
      })) : [],
      tooltip: {
        trigger: "axis", className: "chart-tooltip", confine: true,
        axisPointer: isBar && !m.time
          ? { type: "shadow", shadowStyle: { color: withAlpha(text1.startsWith("#") ? text1 : "#000000", 0.04) } }
          : { type: "line", snap: true, lineStyle: { color: text3, width: 1, type: "solid" } },
        backgroundColor: surface, borderColor: border, borderWidth: 1, padding: [8, 10],
        textStyle: { color: text1, fontSize: 12, fontFamily: font },
        extraCssText: "border-radius:8px;box-shadow:0 4px 16px rgba(0,0,0,.08);",
        formatter: tooltipHtml,
      },
      xAxis: lanes.map((_, i) => ({
        type: m.time ? "time" : "category", gridIndex: i,
        ...(m.time ? { min: m.xMin, max: m.xMax, splitNumber: ticks, minInterval: (m.xMax - m.xMin) / ticks }
          : { data: m.xs, boundaryGap: band }),
        axisLine: { lineStyle: { color: border } }, axisTick: { show: false }, splitLine: { show: false },
        axisLabel: { show: i === lanes.length - 1, color: text3, fontFamily: font, fontSize: 11, margin: 8, hideOverlap: true, formatter: xAxisFmt },
      })),
      yAxis: scales.map((s, i) => ({
        type: "value", gridIndex: i, min: s.min, max: s.max, interval: s.step,
        splitLine: { lineStyle: { color: grid, width: 1 } },
        axisLabel: { color: text3, fontFamily: font, fontSize: 11, margin: 8, formatter: (v: number) => fmt(v, s.format, true) },
      })),
      series: [...m.lines.map((l) => {
        const data = panel.data[l.key], color = colors[l.color];
        const common = { name: l.key, xAxisIndex: l.lane, yAxisIndex: l.lane, data, z: 2 };
        if (isBar) return { ...common, type: "bar", barMaxWidth: 24, barGap: "12%", barCategoryGap: "32%", large: data.length > 2000,
          itemStyle: { color, borderRadius: m.time ? [1, 1, 0, 0] : [4, 4, 0, 0] }, emphasis: { itemStyle: { opacity: 0.85 } } };
        const alone = perLane[l.lane] === 1;
        const gaps = data.some((d) => (Array.isArray(d) ? d[1] : d) === null);
        return { ...common, type: "line", connectNulls: false, step: l.step ? "end" : false,
          sampling: l.step || gaps ? undefined : "lttb", // sampling could drop a reading between gaps
          showSymbol: m.few, symbol: "circle", symbolSize: 8,
          itemStyle: { color, borderColor: surface, borderWidth: 2 },
          lineStyle: { width: 2, cap: "round", join: "round" },
          emphasis: { scale: false, focus: alone ? "none" : "series" },
          ...(alone ? { areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: withAlpha(color, 0.1) }, { offset: 1, color: withAlpha(color, 0) }]) } } : {}) };
      }), ...(isBar || m.few ? [] : m.lines.map((l) => {
        // A reading with no neighbour to connect to draws no line; give it a dot so it is not lost.
        const d = panel.data[l.key];
        const lone = isolated(m.time ? (d as [number, Point][]).map((p) => p[1]) : d as Point[]);
        return { type: "scatter", name: l.key, xAxisIndex: l.lane, yAxisIndex: l.lane, silent: true, z: 3,
          symbolSize: 6, itemStyle: { color: colors[l.color] },
          data: m.time ? (d as [number, Point][]).filter((_, i) => lone[i]) : (d as Point[]).map((v, i) => (lone[i] ? v : null)) };
      })), ...highlightSeries],
    }, true);
  };

  const latest = useRef(render);
  latest.current = render;
  useEffect(() => { if (fontOk) render(); }, [fontOk, m, panel, scales, hidden, shade]);
  useThemeChange(() => { if (fontOk) render(); });
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    // Redraw rather than only resize: lane heights and the number of time ticks follow the size.
    const ro = new ResizeObserver(() => { chart.current?.resize(); if (chart.current) latest.current(); });
    ro.observe(el);
    return () => {
      ro.disconnect();
      if (chart.current) { charts.delete(chart.current); chart.current.dispose(); chart.current = null; }
    };
  }, []);

  return (
    <div className="facet-panel">
      {panel.title && <h3 className="facet-title" title={panel.title}>{panel.title}</h3>}
      <div className="facet-plot" style={height ? { height } : undefined}>
        <div ref={ref} className="chart-plot" role="img"
          aria-label={`${tile.type} chart of ${String(tile.y)} by ${tile.x}${panel.title ? ` for ${panel.title}` : ""}`} />
        <div ref={cross} className="facet-cross" aria-hidden />
      </div>
    </div>
  );
}
