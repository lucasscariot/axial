import type { QueryMeta, Row, Tile } from "../api";
import { defaultFormat, fmt } from "../format";

export function reduce(rows: Row[], col: string, how: string): number | null {
  const vals = rows.map((r) => r[col]).filter((v) => v !== null && v !== undefined).map(Number);
  if (how === "count") return rows.length;
  if (vals.length === 0) return null;
  switch (how) {
    case "sum": return vals.reduce((a, b) => a + b, 0);
    case "min": return Math.min(...vals);
    case "max": return Math.max(...vals);
    case "first": return vals[0];
    case "last": return vals[vals.length - 1];
    default: return rows.length === 1 ? vals[0] : null; // "single": the query returns one row
  }
}

export function KpiTile({ tile, query, rows }: { tile: Tile; query: QueryMeta; rows: Row[] }) {
  const col = tile.value!;
  const how = tile.reduce ?? "single";
  const format = tile.format ?? query.formats[col] ?? defaultFormat(query.columns[col]);
  const v = reduce(rows, col, how);
  const bad = how === "single" && rows.length !== 1;
  return (
    <div className="kpi">
      <div className="kpi-value" title={v === null ? "" : fmt(v, format)}>{bad ? "–" : fmt(v, format, true)}</div>
      {bad && <div className="kpi-note bad">expected 1 row, got {rows.length}</div>}
      {!bad && how !== "single" && <div className="kpi-note muted">{how} of {rows.length} rows</div>}
    </div>
  );
}
