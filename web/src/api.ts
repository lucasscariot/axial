export type Meta = {
  mode: "live" | "fixture";
  engine_version: string;
  project: string;
  display: {
    title: string | null; brand_color: string | null; has_logo: boolean;
    currency: string; locale: string; slow_tile_ms: number; billing_mode: "on_demand" | "reservation" | "local";
    price_per_tib_usd: number; billing_project: string | null; location: string | null; row_cap: number;
    warehouse: "bigquery" | "duckdb"; database: string | null;
  };
};

export type QueryMeta = {
  id: string; title: string; columns: Record<string, string>; formats: Record<string, string>; labels: Record<string, string>;
  params: Record<string, string>; sql_hash: string;
};

export type Tile = {
  type: "kpi" | "line" | "bar" | "funnel" | "table" | "text"; query: string; markdown?: string; span: number; title?: string;
  value?: string; reduce?: string; format?: string; compare?: string;
  x?: string; y?: string | string[]; series?: string; columns?: string[]; formats?: Record<string, string>;
  height?: number;
};

export type DashboardDef = {
  id: string; title: string; description: string;
  date_range: { default_start: string; default_end: string; max_days: number; start_param: string; end_param: string } | null;
  tiles: Tile[]; queries: Record<string, QueryMeta>; errors: string[];
};

export type Row = Record<string, unknown>;

export type Dataset = {
  query_id: string; rows: Row[]; truncated: boolean; total_rows: number; source: string;
  request_id: number | null; job_id: string | null; sql_hash: string; app_ms: number; bq_cache_hit?: boolean | null;
};

export class ApiError extends Error {
  constructor(message: string, public status: number, public detail: any) { super(message); }
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, init);
  if (!r.ok) {
    let detail: any = null;
    try { detail = (await r.json()).detail; } catch { /* not JSON */ }
    const msg = typeof detail === "string" ? detail : detail?.message ?? `${r.status} ${r.statusText}`;
    throw new ApiError(msg, r.status, detail);
  }
  return r.json();
}

export const api = {
  meta: () => request<Meta>("/api/meta"),
  dashboards: () => request<{ id: string; title: string; description: string; tiles: number }[]>("/api/dashboards"),
  dashboard: (id: string) => request<DashboardDef>(`/api/dashboards/${encodeURIComponent(id)}`),
  dataset: (body: { dashboard_id: string; query_id: string; filters: Record<string, string>; bypass_cache?: boolean }, signal?: AbortSignal) =>
    request<Dataset>("/api/datasets", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body), signal }),
  inspect: (dashboard_id: string, query_id: string, filters: Record<string, string>) =>
    request<any>(`/api/inspect?${new URLSearchParams({ dashboard_id, query_id, filters: JSON.stringify(filters) })}`),
  request: (id: number) => request<any>(`/api/requests/${id}`),
  usage: (start: string, end: string) => request<any>(`/api/usage?${new URLSearchParams({ start, end })}`),
  usageQuery: (q: string, start: string, end: string) =>
    request<any>(`/api/usage/${encodeURIComponent(q)}?${new URLSearchParams({ start, end })}`),
};
