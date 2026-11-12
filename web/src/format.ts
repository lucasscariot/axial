import type { Meta } from "./api";

export const NUMERIC = new Set(["INT64", "FLOAT64", "NUMERIC", "BIGNUMERIC"]);

let display: Meta["display"] = {
  title: null, brand_color: null, has_logo: false, currency: "GBP", locale: "en-GB", slow_tile_ms: 3000, billing_mode: "on_demand",
  price_per_tib_usd: 6.25, billing_project: null, location: null, row_cap: 2000,
};
export function setDisplay(d: Meta["display"]) { display = d; }
export function getDisplay() { return display; }

export function defaultFormat(type: string | undefined): string {
  if (type === "INT64") return "integer";
  if (type && NUMERIC.has(type)) return "number";
  if (type === "DATE") return "date";
  return "text";
}

export function duration(seconds: number): string {
  const s = Math.round(Math.abs(seconds));
  const sign = seconds < 0 ? "-" : "";
  if (s < 60) return `${sign}${s}s`;
  if (s < 3600) return `${sign}${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;
  if (s < 86400) return `${sign}${Math.floor(s / 3600)}h ${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}m`;
  return `${sign}${Math.floor(s / 86400)}d ${Math.floor((s % 86400) / 3600)}h`;
}

/** Format a value. `compact` is for KPI headlines and chart axes. */
export function fmt(v: unknown, format: string, compact = false): string {
  if (v === null || v === undefined || v === "") return "–";
  const loc = display.locale;
  const n = typeof v === "number" ? v : Number(v);
  switch (format) {
    case "integer":
      return Number.isFinite(n) ? n.toLocaleString(loc, compact && Math.abs(n) >= 1e5 ? { notation: "compact", maximumFractionDigits: 1 } : { maximumFractionDigits: 0 }) : String(v);
    case "number":
      return Number.isFinite(n) ? n.toLocaleString(loc, compact && Math.abs(n) >= 1e5 ? { notation: "compact", maximumFractionDigits: 1 } : { maximumFractionDigits: 2 }) : String(v);
    case "money":
      return Number.isFinite(n)
        ? n.toLocaleString(loc, { style: "currency", currency: display.currency, ...(compact && Math.abs(n) >= 1e4 ? { notation: "compact", maximumFractionDigits: 1 } : { maximumFractionDigits: compact ? 0 : 2, minimumFractionDigits: compact ? 0 : 2 }) })
        : String(v);
    case "percent": // values are fractions: 0.042 -> 4.2%
      return Number.isFinite(n) ? n.toLocaleString(loc, { style: "percent", maximumFractionDigits: 1, minimumFractionDigits: 1 }) : String(v);
    case "duration": // values are seconds
      return Number.isFinite(n) ? duration(n) : String(v);
    case "date": {
      const s = String(v);
      const d = new Date(s.length === 10 ? s + "T00:00:00" : s);
      return isNaN(+d) ? s : d.toLocaleDateString(loc, { day: "2-digit", month: "short", ...(compact ? {} : { year: "numeric" }) });
    }
    default: {
      const s = String(v);
      if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(s)) {
        const d = new Date(s);
        if (!isNaN(+d)) return d.toLocaleString(loc, { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
      }
      return s;
    }
  }
}

export function bytes(n: number | null | undefined): string {
  if (n === null || n === undefined) return "–";
  const u = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"];
  let i = 0, x = n;
  while (Math.abs(x) >= 1024 && i < u.length - 1) { x /= 1024; i++; }
  return `${i === 0 ? x : x.toFixed(x < 10 ? 2 : 1)} ${u[i]}`;
}

export function ms(n: number | null | undefined): string {
  if (n === null || n === undefined) return "–";
  if (n < 1000) return `${Math.round(n)} ms`;
  return `${(n / 1000).toFixed(n < 10000 ? 2 : 1)} s`;
}

export function pct(n: number | null | undefined): string {
  return n === null || n === undefined ? "–" : `${(n * 100).toFixed(0)}%`;
}

export function usd(n: number | null | undefined): string {
  if (n === null || n === undefined) return "–";
  return n.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: n < 1 ? 4 : 2 });
}

export const addDays = (iso: string, days: number) => {
  const d = new Date(iso + "T00:00:00Z");
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
};
export const todayIso = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};
export const daysBetween = (a: string, b: string) => Math.round((Date.parse(b) - Date.parse(a)) / 86400000);
