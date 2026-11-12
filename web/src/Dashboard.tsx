import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, type Dataset, type DashboardDef, type Meta, type Tile } from "./api";
import { addDays, daysBetween, getDisplay, ms, todayIso } from "./format";
import { setSearch } from "./router";
import { KpiTile } from "./tiles/Kpi";
import { ChartTile } from "./tiles/Chart";
import { TableTile } from "./tiles/Table";
import { Markdown } from "./tiles/Markdown";
import { InspectDrawer, type InspectTarget } from "./Inspect";

type DS = { status: "loading" | "ok" | "error"; data?: Dataset; error?: string; jobId?: string | null; requestId?: number | null; started: number };

const DEFAULT_HEIGHT: Record<Tile["type"], number> = { kpi: 64, line: 200, bar: 200, table: 330, text: 0 };

export function tileTitle(t: Tile, def: DashboardDef) {
  if (t.title) return t.title;
  const q = def.queries[t.query];
  if (t.type === "kpi" && t.value) return t.value.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
  return q?.title ?? t.query;
}

export function DashboardPage({ id, meta }: { id: string; meta: Meta | null }) {
  const [def, setDef] = useState<DashboardDef | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [range, setRange] = useState<{ start: string; end: string } | null>(null); // end exclusive
  const [bypass, setBypass] = useState(false);
  const [data, setData] = useState<Record<string, DS>>({});
  const [inspect, setInspect] = useState<InspectTarget | null>(null);
  const [nonce, setNonce] = useState(0);
  const abort = useRef<AbortController | null>(null);

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

  const onRange = useCallback((start: string, endInclusive: string) => {
    setRange({ start, end: addDays(endInclusive, 1) });
  }, []);

  if (loadErr) return <main className="page"><div className="banner error">{loadErr}</div></main>;
  if (!def || !range) return <main className="page muted">Loading…</main>;

  const dr = def.date_range;
  const rangeDays = dr ? daysBetween(range.start, range.end) : 0;
  const rangeInvalid = dr ? rangeDays < 1 || rangeDays > dr.max_days : false;

  return (
    <main className="page dashboard" data-dashboard-ready={ready ? "true" : "false"}>
      <div className="filterbar">
        <h1 className="dash-title">{def.title}</h1>
        {dr && (
          <>
            <label className="field">
              <span>From</span>
              <input type="date" value={range.start} max={addDays(range.end, -1)} onChange={(e) => e.target.value && onRange(e.target.value, addDays(range.end, -1))} />
            </label>
            <label className="field">
              <span>To</span>
              <input type="date" value={addDays(range.end, -1)} min={range.start} onChange={(e) => e.target.value && onRange(range.start, e.target.value)} />
            </label>
            <div className="presets" role="group" aria-label="Date presets">
              {[1, 7, 14, 28, dr.max_days].filter((n, i, a) => n <= dr.max_days && a.indexOf(n) === i).map((n) => {
                const end = addDays(todayIso(), 1);
                const start = addDays(end, -n);
                const on = range.start === start && range.end === end;
                return <button key={n} className={on ? "on" : ""} onClick={() => setRange({ start, end })}>{n === 1 ? "Today" : `${n}d`}</button>;
              })}
            </div>
            <span className={`muted small ${rangeInvalid ? "bad" : ""}`} title="End date is exclusive in queries">
              {rangeDays} day{rangeDays === 1 ? "" : "s"}{rangeInvalid ? ` (max ${dr.max_days})` : ""}
            </span>
          </>
        )}
        <span className="spacer" />
        <label className="check small" title="Skip the app cache and BigQuery's result cache. Every dataset submits a new, billed job.">
          <input type="checkbox" checked={bypass} onChange={(e) => setBypass(e.target.checked)} /> Bypass caches
        </label>
        <button onClick={() => setNonce((n) => n + 1)} title="Reload datasets (uses cache unless bypassed)">Reload</button>
      </div>
      {def.errors.length > 0 && (
        <div className="banner error"><b>bqdash check</b> found problems:<ul>{def.errors.map((e) => <li key={e}><code>{e}</code></li>)}</ul></div>
      )}
      <div className="grid">
        {def.tiles.map((t, i) => t.type === "text" ? (
          <section key={i} className="tile tile-text" style={{ gridColumn: `span ${Math.min(12, Math.max(1, t.span))}` }}>
            {t.title && <header className="tile-head"><h2>{t.title}</h2></header>}
            <Markdown source={t.markdown ?? ""} />
          </section>
        ) : (
          <TileFrame key={i} tile={t} def={def} ds={data[t.query]} meta={meta}
            onInspect={() => setInspect({ kind: "latest", dashboardId: def.id, queryId: t.query, filters, requestId: data[t.query]?.data?.request_id ?? data[t.query]?.requestId ?? null })} />
        ))}
      </div>
      {inspect && <InspectDrawer target={inspect} onClose={() => setInspect(null)} />}
    </main>
  );
}

function TileFrame({ tile, def, ds, meta, onInspect }: { tile: Tile; def: DashboardDef; ds?: DS; meta: Meta | null; onInspect: () => void }) {
  const q = def.queries[tile.query];
  const height = tile.height ?? DEFAULT_HEIGHT[tile.type] ?? 200;
  const slow = ds?.data && ds.data.app_ms > getDisplay().slow_tile_ms;
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    if (ds?.status !== "loading") return;
    const t = setInterval(() => setElapsed(performance.now() - ds.started), 250);
    return () => clearInterval(t);
  }, [ds?.status, ds?.started]);

  const source = ds?.data?.source;
  const live = meta?.mode === "live";
  return (
    <section className={`tile tile-${tile.type}`} style={{ gridColumn: `span ${Math.min(12, Math.max(1, tile.span))}` }}>
      <header className="tile-head">
        <h2 title={q ? `${q.title} · ${q.id}` : tile.query}>{tileTitle(tile, def)}</h2>
        <span className="tile-meta">
          {ds?.status === "loading" && elapsed > 600 && <span className="muted">{ms(elapsed)}</span>}
          {ds?.data && (
            <span className={slow ? "slow" : "muted"} title={`served from ${source}${ds.data.bq_cache_hit ? " (BigQuery cache hit)" : ""}`}>
              {source === "app_cache" || source === "shared" ? "cached · " : ""}{ms(ds.data.app_ms)}
            </span>
          )}
          {ds?.data?.truncated && <span className="pill warn" title={`Showing ${ds.data.rows.length} of ${ds.data.total_rows} rows`}>truncated</span>}
          {live && <button className="link" onClick={onInspect} title="Inspect query: latest run, timing, job, SQL version">Inspect</button>}
        </span>
      </header>
      <div className="tile-body" style={{ height: tile.type === "kpi" ? undefined : height, minHeight: tile.type === "kpi" ? height : undefined }}>
        {!q && <div className="tile-msg error">Unknown query <code>{tile.query}</code></div>}
        {q && (!ds || ds.status === "loading") && <div className="skeleton" />}
        {q && ds?.status === "error" && (
          <div className="tile-msg error">
            {ds.error}
            {live && ds.requestId && <> · <button className="link" onClick={onInspect}>Inspect</button></>}
          </div>
        )}
        {q && ds?.status === "ok" && ds.data && (
          tile.type === "kpi" ? <KpiTile tile={tile} query={q} rows={ds.data.rows} /> :
          tile.type === "table" ? <TableTile tile={tile} query={q} data={ds.data} /> :
          <ChartTile tile={tile} query={q} rows={ds.data.rows} height={height} />
        )}
      </div>
    </section>
  );
}

