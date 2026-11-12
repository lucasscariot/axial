import { useEffect, useRef, useState } from "react";
import * as echarts from "echarts/core";
import { FunnelChart } from "echarts/charts";
import { TooltipComponent } from "echarts/components";
import { LabelLayout } from "echarts/features";
import { SVGRenderer } from "echarts/renderers";
import type { QueryMeta, Row, Tile } from "../api";
import { defaultFormat, fmt } from "../format";
import { cssVar, esc, fontsReady, useThemeChange } from "./Chart";

echarts.use([FunnelChart, TooltipComponent, LabelLayout, SVGRenderer]);

export type Stage = { name: string; value: number; ofFirst: number | null; ofPrev: number | null };

/** Stages in query order. Rows that repeat a stage are summed. */
export function buildStages(tile: Tile, rows: Row[]): Stage[] {
  const x = tile.x!;
  const y = Array.isArray(tile.y) ? tile.y[0] : tile.y!;
  const totals = new Map<string, number>();
  for (const r of rows) {
    const k = String(r[x] ?? "∅");
    const v = r[y] == null ? 0 : Number(r[y]);
    totals.set(k, (totals.get(k) ?? 0) + (Number.isFinite(v) ? v : 0));
  }
  const entries = [...totals];
  const first = entries[0]?.[1] ?? 0;
  return entries.map(([name, value], i) => {
    const prev = i > 0 ? entries[i - 1][1] : null;
    return {
      name, value,
      ofFirst: first ? value / first : null,
      ofPrev: prev ? value / prev : null,
    };
  });
}

export function FunnelTile({ tile, query, rows, height }: { tile: Tile; query: QueryMeta; rows: Row[]; height: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.ECharts | null>(null);
  const stages = buildStages(tile, rows);
  const yCol = Array.isArray(tile.y) ? tile.y[0] : tile.y!;

  const render = () => {
    const el = ref.current;
    if (!el) return;
    if (!chart.current) chart.current = echarts.init(el, undefined, { renderer: "svg" });
    const xFmt = defaultFormat(query.columns[tile.x!]);
    const yFmt = tile.format ?? query.formats[yCol] ?? defaultFormat(query.columns[yCol]);
    const color = cssVar(el, "--series-1");
    const text1 = cssVar(el, "--text-1");
    const text2 = cssVar(el, "--text-2");
    const text3 = cssVar(el, "--text-3");
    const surface = cssVar(el, "--card");
    const border = cssVar(el, "--border");
    const font = cssVar(el, "--font-sans");
    const pct = (v: number | null) => (v == null ? "–" : fmt(v, "percent"));
    const max = Math.max(0, ...stages.map((s) => s.value));
    // Labels sit to the right of the funnel, so it takes a little over half the width
    const labelWidth = Math.max(96, Math.min(220, el.clientWidth * 0.42));

    chart.current.setOption({
      animation: false,
      textStyle: { fontFamily: font, fontSize: 11, color: text2 },
      tooltip: {
        trigger: "item",
        className: "chart-tooltip",
        backgroundColor: surface, borderColor: border, borderWidth: 1, padding: [8, 10],
        textStyle: { color: text1, fontSize: 12, fontFamily: font },
        extraCssText: "border-radius:8px;box-shadow:0 4px 16px rgba(0,0,0,.08);",
        formatter: (p: any) => {
          const s = stages[p.dataIndex];
          const row = (b: string, label: string) => `<div class="tt-row"><b>${esc(b)}</b><span>${esc(label)}</span></div>`;
          return `<div class="tt-head">${esc(fmt(s.name, xFmt))}</div>` +
            `<div class="tt-row"><i class="tt-key bar" style="background:${color}"></i><b>${esc(fmt(s.value, yFmt))}</b></div>` +
            (p.dataIndex > 0 ? row(pct(s.ofFirst), "of first step") + row(pct(s.ofPrev), "of previous step") : "");
        },
      },
      series: [{
        type: "funnel",
        sort: "none",
        orient: "vertical",
        funnelAlign: "center",
        left: 8, top: 4, bottom: 4, right: labelWidth + 16,
        min: 0, max: max || 1, minSize: "6%", maxSize: "100%", gap: 2,
        data: stages.map((s) => ({ name: s.name, value: s.value })),
        itemStyle: { color, borderWidth: 0 },
        emphasis: { itemStyle: { opacity: 0.85 }, label: { show: true } },
        // Labels line up in one column to the right, joined to their stage by a hairline
        labelLayout: () => ({ x: el.clientWidth - labelWidth, align: "left" }),
        labelLine: { show: true, length: 8, lineStyle: { color: border, width: 1 } },
        label: {
          show: true, position: "right", width: labelWidth, overflow: "truncate",
          formatter: (p: any) => {
            const s = stages[p.dataIndex];
            const share = p.dataIndex > 0 ? `  {pct|${pct(s.ofFirst)}}` : "";
            return `{name|${fmt(s.name, xFmt, true)}}\n{value|${fmt(s.value, yFmt, true)}}${share}`;
          },
          rich: {
            name: { color: text2, fontFamily: font, fontSize: 11.5, lineHeight: 16 },
            value: { color: text1, fontFamily: font, fontSize: 12, fontWeight: 600, lineHeight: 16 },
            pct: { color: text3, fontFamily: font, fontSize: 11, lineHeight: 16 },
          },
        },
      }],
    }, true);
  };

  const [fontOk, setFontOk] = useState(false);
  useEffect(() => { let live = true; fontsReady.then(() => live && setFontOk(true)); return () => { live = false; }; }, []);
  useEffect(() => { if (fontOk) render(); }, [fontOk, tile, query, rows]);
  useThemeChange(() => { if (fontOk) render(); });
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(() => { if (fontOk) render(); chart.current?.resize(); });
    ro.observe(el);
    return () => ro.disconnect();
  }, [fontOk, tile, query, rows]);
  useEffect(() => () => { chart.current?.dispose(); chart.current = null; }, []);

  if (rows.length === 0) return <div className="tile-empty">No rows for this range</div>;
  return (
    <div className="chart" style={{ height }}>
      <div ref={ref} className="chart-plot" role="img" aria-label={`Funnel of ${yCol} by ${tile.x}`} />
    </div>
  );
}
