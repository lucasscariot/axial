import { useEffect, useState } from "react";
import { api } from "./api";
import { bytes, engineName, getDisplay, ms, usd } from "./format";
import { Link } from "./router";
import { IconArrowUpRight, IconX } from "./Icons";

export type InspectTarget =
  | { kind: "latest"; dashboardId: string; queryId: string; filters: Record<string, string>; requestId?: number | null }
  | { kind: "request"; requestId: number };

const when = (s: number | null | undefined) => (s ? new Date(s * 1000).toLocaleString(getDisplay().locale) : "–");

function Row({ k, children }: { k: string; children: React.ReactNode }) {
  return <><dt>{k}</dt><dd>{children}</dd></>;
}

export function InspectDrawer({ target, onClose }: { target: InspectTarget; onClose: () => void }) {
  const [d, setD] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    setD(null); setErr(null);
    // Prefer the exact request that fed the tile; otherwise the latest run for the selected filters.
    const p = target.kind === "request" ? api.request(target.requestId)
      : target.requestId ? api.request(target.requestId)
      : api.inspect(target.dashboardId, target.queryId, target.filters);
    p.then(setD, (e) => setErr(e.message));
  }, [target]);
  useEffect(() => {
    const on = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, [onClose]);

  const req = d?.request;
  const job = d?.job;
  const billing = getDisplay().billing_mode;
  const local = billing === "local";
  return (
    <div className="drawer-backdrop" onClick={onClose}>
      <aside className="drawer" onClick={(e) => e.stopPropagation()} aria-label="Inspect query">
        <header className="drawer-head">
          <div>
            <p className="eyebrow">Inspect</p>
            <h2 className="mono">{req ? req.query_id : "query"}</h2>
          </div>
          <button className="icon-btn" onClick={onClose} aria-label="Close"><IconX /></button>
        </header>
        {err && <div className="banner error">{err}</div>}
        {!d && !err && <div className="muted">Loading…</div>}
        {d && (
          <div className="drawer-body">
            {d.matches_selected_filters === false && <div className="banner warn">No run yet for the selected filters; showing the latest run with other filters.</div>}
            {d.sql_changed_since && <div className="banner warn">The SQL has changed since this run (now <code>{d.current_sql_hash.slice(0, 12)}</code>).</div>}
            <h3>Request</h3>
            <dl className="kv">
              <Row k="When">{when(req.requested_at)}</Row>
              <Row k="Filters">{Object.entries(req.params).map(([k, v]) => <div key={k} className="mono">{k} = {String(v)}</div>)}</Row>
              <Row k="Served by">{req.source === "bigquery" ? `new ${engineName()} job` : req.source === "shared" ? "shared in-flight job" : req.source === "app_cache" ? "app cache" : req.source}{req.bypass_cache ? " (caches bypassed)" : ""}</Row>
              <Row k="Outcome"><span className={`badge ${req.outcome === "ok" ? "green" : "red"}`}>{req.outcome}</span>{req.error ? ` ${req.error}` : ""}</Row>
              <Row k="App time">{ms(req.app_ms)}</Row>
              <Row k="Rows">{req.row_count ?? "–"}{req.truncated ? " (truncated at row cap)" : ""}</Row>
            </dl>
            {job ? (
              <>
                <h3>{engineName()} job</h3>
                <dl className="kv">
                  <Row k="Job">{job.console_url ? <a className="mono ext" href={job.console_url} target="_blank" rel="noreferrer">{job.job_id}<IconArrowUpRight /></a> : <span className="mono">{job.job_id}</span>}</Row>
                  <Row k="Status"><span className={`badge ${job.outcome === "success" ? "green" : job.outcome === "error" ? "red" : "gray"}`}>{job.outcome ?? job.status}</span>{job.error ? ` ${job.error}` : ""}</Row>
                  <Row k="Timing">
                    <TimingBar queue={job.queue_ms} exec={job.exec_ms} app={req.source === "bigquery" ? req.app_ms : null} />
                  </Row>
                  {!local && <>
                    <Row k="BigQuery cache">{job.cache_hit == null ? "–" : job.cache_hit ? "hit" : "miss"}{job.use_query_cache ? "" : " (disabled)"}</Row>
                    <Row k="Bytes">{bytes(job.bytes_processed)} processed · {bytes(job.bytes_billed)} billed</Row>
                    <Row k="Slots">{job.slot_ms == null ? "–" : `${job.slot_ms.toLocaleString()} slot-ms (${job.slot_hours.toFixed(4)} slot-h)`}</Row>
                  </>}
                  {billing === "on_demand" && <Row k="Est. cost">{usd(job.est_cost_usd)} <span className="muted">gross, on-demand</span></Row>}
                  <Row k="SQL version"><span className="mono">{job.sql_hash.slice(0, 12)}</span>{job.app_revision && <span className="muted"> · project commit {job.app_revision}</span>}</Row>
                  <Row k="Engine">{job.engine_version ? `axial ${job.engine_version}` : "–"}</Row>
                </dl>
              </>
            ) : <p className="muted">No {engineName()} job is linked to this request.</p>}
            {job?.sql_text && (<><h3>SQL</h3><pre className="sql">{job.sql_text}</pre></>)}
            <p className="drawer-foot"><Link to={`/usage/${encodeURIComponent(req.query_id)}`}>All runs of {req.query_id} →</Link></p>
          </div>
        )}
      </aside>
    </div>
  );
}

function TimingBar({ queue, exec, app }: { queue: number | null; exec: number | null; app: number | null }) {
  const parts = [
    { k: "queue", v: queue ?? 0, label: `queue ${ms(queue)}` },
    { k: "exec", v: exec ?? 0, label: `execution ${ms(exec)}` },
  ];
  const total = app ?? parts.reduce((a, p) => a + p.v, 0);
  const other = app != null ? Math.max(0, app - (queue ?? 0) - (exec ?? 0)) : 0;
  if (app != null) parts.push({ k: "other", v: other, label: `submit/fetch ${ms(other)}` });
  return (
    <div>
      <div className="timing" aria-hidden>
        {parts.map((p) => <span key={p.k} className={`seg seg-${p.k}`} style={{ flexGrow: Math.max(p.v, total * 0.01) }} />)}
      </div>
      <div className="timing-legend">
        {app != null && <span className="timing-total">{ms(app)} total</span>}
        {parts.map((p) => <span key={p.k} className="timing-label"><i className={`swatch seg-${p.k}`} />{p.label}</span>)}
      </div>
    </div>
  );
}
