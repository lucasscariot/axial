import type { Meta } from "./api";

export const NUMERIC = new Set(["INT64", "FLOAT64", "NUMERIC", "BIGNUMERIC"]);

let display: Meta["display"] = {
  title: null, brand_color: null, has_logo: false, currency: "GBP", locale: "en-GB", slow_tile_ms: 3000, billing_mode: "on_demand",
  price_per_tib_usd: 6.25, billing_project: null, location: null, row_cap: 2000, warehouse: "bigquery", database: null,
};
export function setDisplay(d: Meta["display"]) { display = d; }
export function getDisplay() { return display; }
/** The query engine, as shown in the UI. */
export function engineName() { return display.warehouse === "duckdb" ? "DuckDB" : "BigQuery"; }

/** "bookings_due" -> "Bookings due" */
export function humanize(col: string): string {
  const s = col.replace(/_/g, " ").replace(/\s+/g, " ").trim();
  return s.charAt(0).toUpperCase() + s.slice(1);
}

export function labelOf(query: { labels?: Record<string, string> }, col: string): string {
  return query.labels?.[col] ?? humanize(col);
}

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

const ISO = /^(\d{4})-(\d\d)-(\d\d)(?:[T ](\d\d):(\d\d)(?::(\d\d)(?:\.(\d{1,3}))?)?)?/;

/** Milliseconds for a TIMESTAMP (an instant) or a DATETIME (`civil`: wall-clock time, read as UTC
 *  and always shown in UTC, so 09:00 stays 09:00 for every viewer). */
export function toMs(v: unknown, civil: boolean): number | null {
  if (v === null || v === undefined || v === "") return null;
  const s = String(v);
  if (civil) {
    const m = ISO.exec(s);
    return m ? Date.UTC(+m[1], +m[2] - 1, +m[3], +(m[4] ?? 0), +(m[5] ?? 0), +(m[6] ?? 0), +(m[7] ?? "0").padEnd(3, "0")) : null;
  }
  const t = Date.parse(s.length === 10 ? s + "T00:00:00" : s.replace(" ", "T"));
  return Number.isNaN(t) ? null : t;
}

/** A time on a chart: `axis` gives the day at midnight and the time otherwise; `full` both. */
export function fmtTime(ms: number, civil: boolean, mode: "axis" | "full"): string {
  const d = new Date(ms);
  const timeZone = civil ? "UTC" : undefined;
  const [h, m, s] = civil ? [d.getUTCHours(), d.getUTCMinutes(), d.getUTCSeconds()] : [d.getHours(), d.getMinutes(), d.getSeconds()];
  const loc = display.locale;
  if (mode === "axis") {
    return h === 0 && m === 0 ? d.toLocaleDateString(loc, { day: "numeric", month: "short", timeZone })
      : d.toLocaleTimeString(loc, { hour: "2-digit", minute: "2-digit", timeZone });
  }
  return d.toLocaleString(loc, { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit",
    ...(s ? { second: "2-digit" } : {}), timeZone });
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

type Range = { start: string; end: string }; // ISO dates, end exclusive
const monthIndex = (iso: string) => +iso.slice(0, 4) * 12 + +iso.slice(5, 7) - 1;
/** The first of the month `n` months after the month of `iso`. */
const addMonths = (iso: string, n: number) => {
  const t = monthIndex(iso) + n;
  return `${Math.floor(t / 12)}-${String((t % 12) + 1).padStart(2, "0")}-01`;
};

/** How the arrows step [start, end): by whole calendar months when it is made of them; by the
 *  whole week, month, quarter or year a period "to date" (ending at `endToday`) belongs to, the
 *  smallest first; otherwise by its own length in days. */
function step(r: Range, endToday: string): { months: number } | { days: number } {
  const days = daysBetween(r.start, r.end);
  if (r.start.endsWith("-01") && r.end.endsWith("-01")) return { months: monthIndex(r.end) - monthIndex(r.start) };
  if (r.end === endToday && days > 1) {
    if (new Date(r.start + "T00:00:00Z").getUTCDay() === 1 && days <= 7) return { days: 7 };
    if (r.start.endsWith("-01")) {
      for (const n of [1, 3, 12]) if ((monthIndex(r.start) % n === 0) && addMonths(r.start, n) >= r.end) return { months: n };
    }
  }
  return { days };
}

/** The period before (-1) or after (+1) a range, or null when "next" would start after today.
 *  Going forward it stops at `endToday` (today, exclusive end), so the last step is a period to
 *  date; a step that would exceed `maxDays` falls back to the range's own length. */
export function shiftRange(r: Range, dir: -1 | 1, endToday: string, maxDays: number): Range | null {
  if (dir > 0 && r.end >= endToday) return null;
  const by = (s: ReturnType<typeof step>): Range => {
    if ("months" in s) { const start = addMonths(r.start, dir * s.months); return { start, end: addMonths(start, s.months) }; }
    const start = addDays(r.start, dir * s.days);
    return { start, end: addDays(start, s.days) };
  };
  let out = by(step(r, endToday));
  if (daysBetween(out.start, out.end) > maxDays) out = by({ days: daysBetween(r.start, r.end) });
  return dir > 0 && out.end > endToday ? { start: out.start, end: endToday } : out;
}
