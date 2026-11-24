import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { api, type Dataset, type DashboardDef, type Meta, type Tile } from "./api";
import { addDays, daysBetween, engineName, getDisplay, humanize, labelOf, ms } from "./format";
import { IconDownload, IconInfo, IconInspect, IconReload } from "./Icons";
import { setSearch } from "./router";
import { KpiTile } from "./tiles/Kpi";
import { ChartTile } from "./tiles/Chart";
import { PanelsChart, panelHeight, usesPanels } from "./tiles/Panels";
import { FunnelTile } from "./tiles/Funnel";
import { TableTile } from "./tiles/Table";
import { Markdown } from "./tiles/Markdown";
import { InspectDrawer, type InspectTarget } from "./Inspect";
import { enterPrintMode, pdfTitle, settle } from "./print";

type DS = { status: "loading" | "ok" | "error"; data?: Dataset; error?: string; jobId?: string | null; requestId?: number | null; started: number };

const DEFAULT_HEIGHT: Record<Tile["type"], number> = { kpi: 0, line: 220, bar: 220, funnel: 260, table: 360, text: 0 };

export function tileTitle(t: Tile, def: DashboardDef) {
  if (t.title) return t.title;
  const q = def.queries[t.query];
  if (t.type === "kpi" && t.value) return q ? labelOf(q, t.value) : humanize(t.value);
  return q?.title ?? t.query;
}

// --span lets print mode restore the column span that narrow-screen CSS overrides
function spanStyle(span: number) {
  const v = `span ${Math.min(12, Math.max(1, span))}`;
  return { gridColumn: v, "--span": v } as CSSProperties;
}

const fmtDay = (iso: string) =>
  new Date(iso + "T00:00:00").toLocaleDateString(getDisplay().locale, { day: "numeric", month: "short" });

export function DashboardPage({ id, meta }: { id: string; meta: Meta | null }) {
  const [def, setDef] = useState<DashboardDef | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [range, setRange] = useState<{ start: string; end: string } | null>(null); // end exclusive
  const [bypass, setBypass] = useState(false);
  const [data, setData] = useState<Record<string, DS>>({});
  const [inspect, setInspect] = useState<InspectTarget | null>(null);
  const [nonce, setNonce] = useState(0);
  const abort = useRef<AbortController | null>(null);
  // "auto" (?print=1, used by `axial preview --pdf`) lays the page out for print without
  // opening the dialog; "dialog" is the Export PDF button.
  const [printing, setPrinting] = useState<"off" | "auto" | "dialog">(
    () => new URLSearchParams(window.location.search).get("print") === "1" ? "auto" : "off");
  const [printReady, setPrintReady] = useState(false);

  useEffect(() => {
    api.dashboard(id).then((d) => {
      setDef(d);
      document.title = d.title;
      const sp = new URLSearchParams(window.location.search);
      if (d.date_range) setRange({ start: sp.get("start") ?? d.date_range.default_start, end: sp.get("end") ?? d.date_range.default_end });
      else setRange({ start: "", end: "" });
    }, (e) => setLoadErr(e.message));
  }, [id]);

  const filters = useMemo(() => {
    if (!def?.date_range || !range) return {};
    return { [def.date_range.start_param]: range.start, [def.date_range.end_param]: range.end };
  }, [def, range]);

  useEffect(() => {
    if (!def || !range) return;
    if (def.date_range) setSearch({ start: range.start, end: range.end });
    abort.current?.abort();
    const ac = new AbortController();
    abort.current = ac;
    const queryIds = [...new Set(def.tiles.map((t) => t.query))].filter((q) => def.queries[q]);
    const started = performance.now();
    setData(Object.fromEntries(queryIds.map((q) => [q, { status: "loading", started }])));
    // One request per dataset; tiles that share a query share its result. Each renders on arrival.
    for (const q of queryIds) {
      api.dataset({ dashboard_id: def.id, query_id: q, filters, bypass_cache: bypass }, ac.signal).then(
        (d) => setData((s) => ({ ...s, [q]: { status: "ok", data: d, started } })),
        (e) => {
          if (e.name === "AbortError") return;
          setData((s) => ({ ...s, [q]: { status: "error", error: e.message, jobId: e.detail?.job_id, requestId: e.detail?.request_id, started } }));
        },
      );
    }
    return () => ac.abort();
  }, [def, range, filters, bypass, nonce]);

  const ready = def !== null && Object.values(data).length > 0 && Object.values(data).every((d) => d.status !== "loading");
  const loading = Object.values(data).some((d) => d.status === "loading");

  useEffect(() => {
    if (printing === "off" || !ready || !def || !range) return;
    setInspect(null);
    const restore = enterPrintMode(pdfTitle(def.title, range.start || undefined, range.end ? addDays(range.end, -1) : undefined));
    let cancelled = false;
    let restored = false;
    const done = () => {
      if (restored) return;
      restored = true;
      restore();
      setPrintReady(false);
      setPrinting("off");
    };
    settle().then(() => {
      if (cancelled) return;
      setPrintReady(true);
      if (printing !== "dialog") return;
      window.addEventListener("afterprint", done, { once: true });
      window.print();
      // Chrome and Safari block in print() until the dialog closes; afterprint covers the rest.
      setTimeout(done, 0);
    });
    return () => {
      cancelled = true;
      window.removeEventListener("afterprint", done);
      if (printing === "dialog") done();
      else if (!restored) { restored = true; restore(); }
    };
  }, [printing, ready, def, range]);

  // Cmd/Ctrl+P goes through the same path, so the PDF gets light colours and a print layout.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && !e.shiftKey && !e.altKey && e.key.toLowerCase() === "p" && ready) {
        e.preventDefault();
        setPrinting("dialog");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [ready]);

  const onRange = useCallback((start: string, endInclusive: string) => {
    setRange({ start, end: addDays(endInclusive, 1) });
  }, []);

  // The engine dates each period when it sends the definition. A page left open past midnight
  // holds yesterday's dates, so ask again before applying one; fall back to what we have.
  const onPreset = useCallback((presetId: string, start: string, end: string) => {
    api.dashboard(id).then((d) => {
      const fresh = d.date_range?.presets.find((p) => p.id === presetId);
      setDef(d);
      setRange(fresh ? { start: fresh.start, end: fresh.end } : { start, end });
    }, () => setRange({ start, end }));
  }, [id]);

  if (loadErr) return <main className="page"><div className="banner error">{loadErr}</div></main>;
  if (!def || !range) return <main className="page"><div className="page-head"><div className="skeleton-line" /></div></main>;

  const dr = def.date_range;
  const rangeDays = dr ? daysBetween(range.start, range.end) : 0;
  const rangeInvalid = dr ? rangeDays < 1 || rangeDays > dr.max_days : false;

  return (
    <main className="page dashboard" data-dashboard-ready={ready ? "true" : "false"} data-print-ready={printReady ? "true" : "false"}>
      <header className="page-head">
        <div className="page-head-text">
          <p className="print-only print-brand">{meta?.display.title ?? meta?.project}</p>
          <h1 className="page-title">{def.title}</h1>
          {dr && (
            <p className="page-sub">
              {rangeDays === 1 ? fmtDay(range.start) : `${fmtDay(range.start)} – ${fmtDay(addDays(range.end, -1))}`}
              <span className={`dot-sep ${rangeInvalid ? "bad" : ""}`}>
                {rangeDays} day{rangeDays === 1 ? "" : "s"}{rangeInvalid ? ` · max ${dr.max_days}` : ""}
              </span>
            </p>
          )}
          {def.description && <p className="page-desc">{def.description}</p>}
          <p className="print-only print-meta">
            Generated {new Date().toLocaleString(getDisplay().locale, { dateStyle: "medium", timeStyle: "short" })}
            {meta?.mode === "fixture" && <span className="dot-sep">Representative fixture data, not real figures</span>}
            {meta?.mode === "live" && (meta.display.billing_project || meta.display.database) && <span className="dot-sep">{engineName()} · {meta.display.billing_project ?? meta.display.database}</span>}
          </p>
        </div>
      </header>

      <div className="toolbar">
        {dr && (
          <>
            <div className="segmented" role="group" aria-label="Date range presets">
              {dr.presets.map((p) => {
                const on = range.start === p.start && range.end === p.end;
                return (
                  <button key={p.id} aria-pressed={on} className={on ? "on" : ""}
                    onClick={() => onPreset(p.id, p.start, p.end)}>
                    {p.label}
                  </button>
                );
              })}
            </div>
            <div className="date-range" title="End date is inclusive here; queries receive an exclusive end_date">
              <input type="date" aria-label="From" value={range.start} max={addDays(range.end, -1)}
                onChange={(e) => e.target.value && onRange(e.target.value, addDays(range.end, -1))} />
              <span className="date-arrow" aria-hidden>→</span>
              <input type="date" aria-label="To" value={addDays(range.end, -1)} min={range.start}
                onChange={(e) => e.target.value && onRange(range.start, e.target.value)} />
            </div>
          </>
        )}
        <span className="spacer" />
        <label className="switch" title={getDisplay().billing_mode === "local" ? "Skip the app cache. Every dataset runs a new query." : "Skip the app cache and BigQuery's result cache. Every dataset submits a new, billed job."}>
          <input type="checkbox" role="switch" checked={bypass} onChange={(e) => setBypass(e.target.checked)} />
          <span className="switch-track" aria-hidden><span className="switch-thumb" /></span>
          Bypass caches
        </label>
        <button className="btn" onClick={() => setNonce((n) => n + 1)} title="Reload datasets (uses the cache unless bypassed)">
          <IconReload className={loading ? "spin" : ""} /> Reload
        </button>
        <button className="btn" onClick={() => setPrinting("dialog")} disabled={!ready || printing !== "off"}
          title={ready ? "Export as PDF: choose “Save as PDF” in the print dialog" : "Wait for every tile to load"}>
          <IconDownload /> Export PDF
        </button>
      </div>

      {def.errors.length > 0 && (
        <div className="banner error"><b>axial check</b> found problems<ul>{def.errors.map((e) => <li key={e}><code>{e}</code></li>)}</ul></div>
      )}

      <div className="grid">
        {def.tiles.map((t, i) => t.type === "text" ? (
          <section key={i} className="note" style={spanStyle(t.span)}>
            <IconInfo className="note-icon" />
            <div>
              {t.title && <h2 className="note-title">{t.title}</h2>}
              <Markdown source={t.markdown ?? ""} />
            </div>
          </section>
        ) : (
          <TileFrame key={i} tile={t} def={def} ds={data[t.query]} meta={meta} range={range}
            onInspect={() => setInspect({ kind: "latest", dashboardId: def.id, queryId: t.query, filters, requestId: data[t.query]?.data?.request_id ?? data[t.query]?.requestId ?? null })} />
        ))}
      </div>
      {inspect && <InspectDrawer target={inspect} onClose={() => setInspect(null)} />}
    </main>
  );
}

type Range = { start: string; end: string };

function TileFrame({ tile, def, ds, meta, range, onInspect }: { tile: Tile; def: DashboardDef; ds?: DS; meta: Meta | null; range: Range; onInspect: () => void }) {
  const q = def.queries[tile.query];
  const panels = !!q && (tile.type === "line" || tile.type === "bar") && usesPanels(tile, q);
  const height = panels ? panelHeight(tile) : tile.height ?? DEFAULT_HEIGHT[tile.type] ?? 220;
  // a time axis spans the selected dates when the query is filtered by them
  const dr = def.date_range;
  const xRange = dr && q && dr.start_param in q.params ? range : undefined;
  const faceted = panels && !!tile.facet;
  const slow = !!ds?.data && ds.data.app_ms > getDisplay().slow_tile_ms;
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    if (ds?.status !== "loading") return;
    const t = setInterval(() => setElapsed(performance.now() - ds.started), 250);
    return () => clearInterval(t);
  }, [ds?.status, ds?.started]);

  const source = ds?.data?.source;
  const cached = source === "app_cache" || source === "shared";
  const live = meta?.mode === "live";
  const title = tileTitle(tile, def);
  return (
    <section className={`card tile tile-${tile.type}${faceted ? " tile-faceted" : ""}${slow ? " is-slow" : ""}`} style={spanStyle(tile.span)}>
      <header className="tile-head">
        <h2 title={q ? `${q.title} · ${q.id}` : tile.query}>{title}</h2>
        <div className="tile-meta">
          {ds?.status === "loading" && elapsed > 600 && <span className="time">{ms(elapsed)}</span>}
          {ds?.data && (
            <span className={`time ${slow ? "slow" : ""}`} title={`Served from ${source}${ds.data.bq_cache_hit ? " (BigQuery cache hit)" : ""} in ${ms(ds.data.app_ms)}`}>
              {slow && <span className="slow-dot" aria-hidden />}
              {cached ? "cached" : ms(ds.data.app_ms)}
            </span>
          )}
          {ds?.data?.truncated && <span className="badge amber" title={`Showing ${ds.data.rows.length} of ${ds.data.total_rows} rows`}>Truncated</span>}
          {live && (
            <button className="icon-btn sm reveal" onClick={onInspect} aria-label={`Inspect ${title}`}
              title="Inspect: latest run, timing, BigQuery job, SQL version">
              <IconInspect />
            </button>
          )}
        </div>
      </header>
      <div className="tile-body" style={tile.type === "kpi" || (faceted && ds?.status === "ok") ? undefined : { height }}>
        {!q && <div className="tile-msg error">Unknown query <code>{tile.query}</code></div>}
        {q && (!ds || ds.status === "loading") && <div className={`skeleton ${tile.type === "kpi" ? "kpi-skeleton" : ""}`} />}
        {q && ds?.status === "error" && (
          <div className="tile-msg error">
            <span>{ds.error}</span>
            {live && ds.requestId && <button className="link" onClick={onInspect}>Inspect run</button>}
          </div>
        )}
        {q && ds?.status === "ok" && ds.data && (
          tile.type === "kpi" ? <KpiTile tile={tile} query={q} rows={ds.data.rows} /> :
          tile.type === "table" ? <TableTile tile={tile} query={q} data={ds.data} /> :
          tile.type === "funnel" ? <FunnelTile tile={tile} query={q} rows={ds.data.rows} height={height} /> :
          panels ? <PanelsChart tile={tile} query={q} rows={ds.data.rows} height={height} range={xRange} /> :
          <ChartTile tile={tile} query={q} rows={ds.data.rows} height={height} />
        )}
      </div>
    </section>
  );
}
