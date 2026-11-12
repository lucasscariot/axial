import { useEffect, useState } from "react";
import { api, type Meta } from "./api";
import { setDisplay } from "./format";
import { IconArrowUpRight, IconMonitor, IconMoon, IconSun } from "./Icons";
import { Link, useLocation } from "./router";
import { DashboardPage } from "./Dashboard";
import { UsagePage, UsageQueryPage } from "./Usage";
import { applyTheme, readTheme, type ThemePref } from "./theme";

function Home({ meta }: { meta: Meta | null }) {
  const [list, setList] = useState<Awaited<ReturnType<typeof api.dashboards>> | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { api.dashboards().then(setList, (e) => setErr(String(e.message))); }, []);
  useEffect(() => { document.title = meta?.display.title ?? "Dashboards"; }, [meta]);
  return (
    <main className="page">
      <header className="page-head">
        <div>
          <h1 className="page-title">Dashboards</h1>
          <p className="page-sub">{list ? `${list.length} in dashboards/` : " "}</p>
        </div>
      </header>
      {err && <div className="banner error">{err}</div>}
      <div className="card-list">
        {list?.map((d) => (
          <Link key={d.id} to={`/d/${d.id}`} className="card-link">
            <span className="card-link-title">{d.title}<IconArrowUpRight className="card-link-arrow" /></span>
            {d.description && <span className="card-link-desc">{d.description}</span>}
            <span className="card-link-meta"><code>{d.id}</code> · {d.tiles} tiles</span>
          </Link>
        ))}
        {list && list.length === 0 && <p className="muted">No dashboards in <code>dashboards/</code> yet.</p>}
      </div>
    </main>
  );
}

const NEXT: Record<ThemePref, ThemePref> = { system: "light", light: "dark", dark: "system" };

function ThemeToggle() {
  const [pref, setPref] = useState<ThemePref>(readTheme());
  const Icon = pref === "light" ? IconSun : pref === "dark" ? IconMoon : IconMonitor;
  const next = NEXT[pref];
  return (
    <button className="icon-btn" aria-label={`Theme: ${pref}. Switch to ${next}`} title={`Theme: ${pref}`}
      onClick={() => { applyTheme(next); setPref(next); }}>
      <Icon />
    </button>
  );
}

export function App() {
  const { path } = useLocation();
  const [meta, setMeta] = useState<Meta | null>(null);
  useEffect(() => {
    api.meta().then((m) => {
      setDisplay(m.display);
      if (m.display.brand_color) document.documentElement.style.setProperty("--brand", m.display.brand_color);
      setMeta(m);
    }, () => setMeta(null));
  }, []);

  let page;
  let m: RegExpMatchArray | null;
  if ((m = path.match(/^\/d\/([^/]+)/))) page = <DashboardPage key={m[1]} id={decodeURIComponent(m[1])} meta={meta} />;
  else if ((m = path.match(/^\/usage\/([^/]+)/))) page = <UsageQueryPage key={m[1]} queryId={decodeURIComponent(m[1])} meta={meta} />;
  else if (path.startsWith("/usage")) page = <UsagePage meta={meta} />;
  else page = <Home meta={meta} />;

  const title = meta?.display.title ?? meta?.project ?? "Dashboards";
  const onUsage = path.startsWith("/usage");
  return (
    <div className="app">
      <nav className="topnav">
        <div className="topnav-inner">
          <Link to="/" className="brand">
            {meta?.display.has_logo
              ? <img src="/api/brand/logo" alt="" className="logo" />
              : <span className="mark" aria-hidden>{title.charAt(0).toUpperCase()}</span>}
            <span>{title}</span>
          </Link>
          <div className="tabs" role="navigation" aria-label="Sections">
            <Link to="/" className={`tab ${!onUsage ? "active" : ""}`}>Dashboards</Link>
            <Link to="/usage" className={`tab ${onUsage ? "active" : ""}`}>Usage</Link>
          </div>
          <span className="spacer" />
          {meta?.mode === "fixture" && <span className="badge amber" title="Representative data; no warehouse queries">Fixture data</span>}
          {meta?.display.billing_project && (
            <span className="nav-meta" title="Billing project · location">{meta.display.billing_project}<span className="sep">/</span>{meta.display.location}</span>
          )}
          {meta && <span className="nav-meta" title="bqdash engine version">v{meta.engine_version}</span>}
          <ThemeToggle />
        </div>
      </nav>
      {page}
    </div>
  );
}
