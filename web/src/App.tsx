import { useEffect, useState } from "react";
import { api, type Meta } from "./api";
import { setDisplay } from "./format";
import { Link, useLocation } from "./router";
import { DashboardPage } from "./Dashboard";
import { UsagePage, UsageQueryPage } from "./Usage";

function Home() {
  const [list, setList] = useState<Awaited<ReturnType<typeof api.dashboards>> | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { api.dashboards().then(setList, (e) => setErr(String(e.message))); }, []);
  useEffect(() => { document.title = "Dashboards"; }, []);
  return (
    <main className="page">
      <h1 className="page-title">Dashboards</h1>
      {err && <div className="banner error">{err}</div>}
      <ul className="dash-list">
        {list?.map((d) => (
          <li key={d.id}>
            <Link to={`/d/${d.id}`}>{d.title}</Link>
            <span className="muted"> · {d.tiles} tiles · <code>{d.id}</code></span>
            {d.description && <div className="muted small">{d.description}</div>}
          </li>
        ))}
        {list && list.length === 0 && <li className="muted">No dashboards in dashboards/ yet.</li>}
      </ul>
    </main>
  );
}

export function App() {
  const { path } = useLocation();
  const [meta, setMeta] = useState<Meta | null>(null);
  useEffect(() => { api.meta().then((m) => { setDisplay(m.display); setMeta(m); }, () => setMeta(null)); }, []);

  let page;
  let m: RegExpMatchArray | null;
  if ((m = path.match(/^\/d\/([^/]+)/))) page = <DashboardPage key={m[1]} id={decodeURIComponent(m[1])} meta={meta} />;
  else if ((m = path.match(/^\/usage\/([^/]+)/))) page = <UsageQueryPage key={m[1]} queryId={decodeURIComponent(m[1])} meta={meta} />;
  else if (path.startsWith("/usage")) page = <UsagePage meta={meta} />;
  else page = <Home />;

  return (
    <div className="app">
      <nav className="topnav">
        <Link to="/" className="brand">Dashboards</Link>
        <Link to="/usage" className={path.startsWith("/usage") ? "active" : ""}>Query usage</Link>
        <span className="spacer" />
        {meta?.mode === "fixture" && <span className="pill warn" title="Representative data; no warehouse queries">Fixture data</span>}
        {meta?.display.billing_project && <span className="muted small">{meta.display.billing_project} · {meta.display.location}</span>}
      </nav>
      {page}
    </div>
  );
}
