import { useEffect, useState } from "react";
import { api, type Meta } from "./api";
import { addDays, bytes, getDisplay, ms, pct, todayIso, usd } from "./format";
import { InspectDrawer } from "./Inspect";
import { Link, setSearch } from "./router";

function useRange() {
  const sp = new URLSearchParams(window.location.search);
  const end0 = addDays(todayIso(), 1);
  const [range, setRange] = useState({ start: sp.get("start") ?? addDays(end0, -7), end: sp.get("end") ?? end0 });
  useEffect(() => setSearch({ start: range.start, end: range.end }), [range]);
  return [range, setRange] as const;
}

function RangeBar({ range, setRange, title, sub }: { range: { start: string; end: string }; setRange: (r: { start: string; end: string }) => void; title: React.ReactNode; sub?: React.ReactNode }) {
  const end0 = addDays(todayIso(), 1);
  return (
    <>
      <header className="page-head">
        <div className="page-head-text">
          <h1 className="page-title">{title}</h1>
          {sub && <p className="page-sub">{sub}</p>}
        </div>
      </header>
      <div className="toolbar">
        <div className="segmented" role="group" aria-label="Date range presets">
          {[1, 7, 30, 90].map((n) => {
            const r = { start: addDays(end0, -n), end: end0 };
            const on = range.start === r.start && range.end === r.end;
            return <button key={n} aria-pressed={on} className={on ? "on" : ""} onClick={() => setRange(r)}>{n === 1 ? "Today" : `${n}d`}</button>;
          })}
        </div>
        <div className="date-range">
          <input type="date" aria-label="From" value={range.start} onChange={(e) => e.target.value && setRange({ ...range, start: e.target.value })} />
          <span className="date-arrow" aria-hidden>→</span>
          <input type="date" aria-label="To" value={addDays(range.end, -1)} onChange={(e) => e.target.value && setRange({ ...range, end: addDays(e.target.value, 1) })} />
        </div>
      </div>
    </>
  );
}

function FixtureNote({ meta }: { meta: Meta | null }) {
  return meta?.mode === "fixture" ? <div className="banner warn">Fixture mode records no execution history. Run <code>bqdash up</code> for live usage.</div> : null;
}

export function UsagePage({ meta }: { meta: Meta | null }) {
  const [range, setRange] = useRange();
  const [d, setD] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { document.title = "Query usage"; }, []);
  useEffect(() => { if (meta?.mode === "live") api.usage(range.start, range.end).then(setD, (e) => setErr(e.message)); }, [range, meta]);
  const onDemand = getDisplay().billing_mode === "on_demand";
  return (
    <main className="page">
      <RangeBar range={range} setRange={setRange} title="Usage" sub="Requests, BigQuery jobs and cost per saved query" />
      <FixtureNote meta={meta} />
      {err && <div className="banner error">{err}</div>}
      {d && (
        <div className="card flush">
          <div className="table-wrap">
            <table className="dense">
              <thead>
                <tr>
                  <th>Saved query</th><th className="num">Requests</th><th className="num">Jobs</th>
                  <th className="num" title="Requests served from the app cache or a shared in-flight job">App cache</th>
                  <th className="num" title="Jobs answered from BigQuery's result cache">BQ cache</th>
                  <th className="num" title="p95 time the app took to serve the dataset">p95 response</th>
                  <th className="num" title="p95 BigQuery execution time (start to end)">p95 BQ exec</th>
                  {onDemand ? <><th className="num">Bytes billed</th><th className="num" title={`Gross estimate at $${d.price_per_tib_usd}/TiB`}>Est. cost</th></> : <th className="num">Slot-hours</th>}
                  <th className="num">Failed</th>
                </tr>
              </thead>
              <tbody>
                {d.queries.map((q: any) => (
                  <tr key={q.query_id}>
                    <td><Link className="mono" to={`/usage/${encodeURIComponent(q.query_id)}?start=${range.start}&end=${range.end}`}>{q.query_id}</Link></td>
                    <td className="num">{q.requests}</td>
                    <td className="num">{q.jobs}{q.jobs_unfinished ? <span className="muted"> ({q.jobs_unfinished} running)</span> : ""}</td>
                    <td className="num">{pct(q.app_cache_hit_rate)}</td>
                    <td className="num">{pct(q.bq_cache_hit_rate)}</td>
                    <td className="num">{ms(q.p95_app_ms)}</td>
                    <td className="num">{ms(q.p95_bq_exec_ms)}</td>
                    {onDemand ? <><td className="num">{bytes(q.bytes_billed)}</td><td className="num">{usd(q.est_cost_usd)}</td></> : <td className="num">{q.slot_hours.toFixed(3)}</td>}
                    <td className={`num ${q.errors || q.jobs_failed ? "bad" : ""}`}>{q.errors + q.jobs_failed || ""}</td>
                  </tr>
                ))}
                {d.queries.length === 0 && <tr><td colSpan={10} className="muted">No requests in this range.</td></tr>}
              </tbody>
              {d.queries.length > 0 && (
                <tfoot>
                  <tr>
                    <td>Total</td><td className="num">{d.totals.requests}</td><td className="num">{d.totals.jobs}</td><td /><td className="num">{pct(d.totals.bq_cache_hit_rate)}</td><td /><td />
                    {onDemand ? <><td className="num">{bytes(d.totals.bytes_billed)}</td><td className="num">{usd(d.totals.est_cost_usd)}</td></> : <td className="num">{d.totals.slot_hours.toFixed(3)}</td>}
                    <td />
                  </tr>
                </tfoot>
              )}
            </table>
          </div>
        </div>
      )}
    </main>
  );
}

const SOURCE_LABEL: Record<string, string> = { bigquery: "job", shared: "shared", app_cache: "app cache" };

export function UsageQueryPage({ queryId, meta }: { queryId: string; meta: Meta | null }) {
  const [range, setRange] = useRange();
  const [d, setD] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const [inspect, setInspect] = useState<number | null>(null);
  useEffect(() => { document.title = `${queryId} · usage`; }, [queryId]);
  useEffect(() => { if (meta?.mode === "live") api.usageQuery(queryId, range.start, range.end).then(setD, (e) => setErr(e.message)); }, [range, meta, queryId]);
  const onDemand = getDisplay().billing_mode === "on_demand";
  return (
    <main className="page">
      <RangeBar range={range} setRange={setRange}
        title={<><Link to={`/usage?start=${range.start}&end=${range.end}`} className="crumb">Usage</Link><span className="crumb-sep">/</span><span className="mono">{queryId}</span></>}
        sub={d?.title} />
      <FixtureNote meta={meta} />
      {err && <div className="banner error">{err}</div>}
      {d && (
        <>
          <section className="card flush">
            <header className="card-head"><h2>SQL versions</h2><p>Compare like with like: same filters, BigQuery cache bypassed (<code>bqdash bench</code> or “Bypass caches”).</p></header>
            <div className="table-wrap">
              <table className="dense">
                <thead><tr><th>SQL</th><th>Filters</th><th>BQ cache</th><th className="num">Runs</th><th className="num">Median exec</th><th className="num">Median billed</th><th className="num">Median slot-ms</th><th>Last run</th><th>Repo</th></tr></thead>
                <tbody>
                  {d.versions.map((v: any, i: number) => (
                    <tr key={i}>
                      <td className="mono">{v.sql_hash.slice(0, 12)}{v.sql_hash === d.current_sql_hash && <span className="badge blue">Current</span>}</td>
                      <td className="small">{Object.entries(v.params).map(([k, x]) => `${k}=${x}`).join(" ")}</td>
                      <td>{v.bq_cache_bypassed ? "bypassed" : `${v.bq_cache_hits}/${v.runs} hits`}</td>
                      <td className="num">{v.runs}</td>
                      <td className="num">{ms(v.median_exec_ms)}</td>
                      <td className="num">{bytes(v.median_bytes_billed)}</td>
                      <td className="num">{v.median_slot_ms?.toLocaleString() ?? "–"}</td>
                      <td>{new Date(v.last_seen * 1000).toLocaleString(getDisplay().locale)}</td>
                      <td className="small muted">{v.app_revision ?? "–"}</td>
                    </tr>
                  ))}
                  {d.versions.length === 0 && <tr><td colSpan={9} className="muted">No finished jobs in this range.</td></tr>}
                </tbody>
              </table>
            </div>
          </section>
          <section className="card flush">
            <header className="card-head"><h2>Runs</h2><p>{d.runs.length} requests · {d.totals.jobs} jobs{onDemand ? ` · ${bytes(d.totals.bytes_billed)} billed · ${usd(d.totals.est_cost_usd)}` : ` · ${d.totals.slot_hours.toFixed(3)} slot-h`}</p></header>
            <div className="table-wrap">
              <table className="dense">
                <thead><tr><th>When</th><th>Dashboard</th><th>Filters</th><th>Served by</th><th>Outcome</th><th className="num">App time</th><th className="num">Queue</th><th className="num">Exec</th><th className="num">Billed</th><th className="num">Slot-ms</th><th>BQ cache</th><th>SQL</th><th>Job</th></tr></thead>
                <tbody>
                  {d.runs.map((r: any) => (
                    <tr key={r.id} className="clickable" onClick={() => setInspect(r.id)}>
                      <td>{new Date(r.requested_at * 1000).toLocaleString(getDisplay().locale)}</td>
                      <td>{r.dashboard_id ?? <span className="muted">cli</span>}</td>
                      <td className="small">{Object.entries(JSON.parse(r.params_json)).map(([k, x]) => `${k}=${x}`).join(" ")}</td>
                      <td>{SOURCE_LABEL[r.source] ?? r.source}{r.bypass_cache ? " · bypass" : ""}</td>
                      <td className={r.outcome === "ok" ? "" : "bad"}>{r.outcome}</td>
                      <td className="num">{ms(r.app_ms)}</td>
                      <td className="num">{ms(r.job?.queue_ms)}</td>
                      <td className="num">{ms(r.job?.exec_ms)}</td>
                      <td className="num">{bytes(r.job?.bytes_billed)}</td>
                      <td className="num">{r.job?.slot_ms?.toLocaleString() ?? "–"}</td>
                      <td>{r.job?.cache_hit == null ? "–" : r.job.cache_hit ? "hit" : "miss"}</td>
                      <td className="mono">{r.sql_hash.slice(0, 8)}</td>
                      <td className="small">{r.job?.console_url ? <a href={r.job.console_url} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}>{r.job.status}</a> : r.job?.status ?? "–"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}
      {inspect !== null && <InspectDrawer target={{ kind: "request", requestId: inspect }} onClose={() => setInspect(null)} />}
    </main>
  );
}
