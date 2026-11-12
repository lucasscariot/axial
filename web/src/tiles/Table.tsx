import { useMemo, useState } from "react";
import type { Dataset, QueryMeta, Tile } from "../api";
import { NUMERIC, defaultFormat, fmt, labelOf } from "../format";
import { IconSort } from "../Icons";

export function TableTile({ tile, query, data }: { tile: Tile; query: QueryMeta; data: Dataset }) {
  const cols = tile.columns ?? Object.keys(query.columns);
  const [sort, setSort] = useState<{ col: string; dir: 1 | -1 } | null>(null);
  const rows = useMemo(() => {
    if (!sort) return data.rows;
    const numeric = NUMERIC.has(query.columns[sort.col]);
    return [...data.rows].sort((a, b) => {
      const x = a[sort.col], y = b[sort.col];
      if (x == null) return 1;
      if (y == null) return -1;
      return (numeric ? Number(x) - Number(y) : String(x).localeCompare(String(y))) * sort.dir;
    });
  }, [data.rows, sort, query.columns]);

  const fmtOf = (c: string) => tile.formats?.[c] ?? query.formats[c] ?? defaultFormat(query.columns[c]);
  const onSort = (c: string) => setSort((s) => (s?.col === c ? (s.dir === -1 ? null : { col: c, dir: -1 }) : { col: c, dir: 1 }));

  if (rows.length === 0) return <div className="tile-empty">No rows for this range</div>;
  return (
    <div className="table-tile">
    <div className="table-wrap">
      <table className="dense">
        <thead>
          <tr>
            {cols.map((c) => (
              <th key={c} className={NUMERIC.has(query.columns[c]) ? "num" : ""} aria-sort={sort?.col === c ? (sort.dir === 1 ? "ascending" : "descending") : "none"}>
                {/* Chrome drops button content from table headers repeated on printed pages */}
                <span className="print-only th-print">{labelOf(query, c)}</span>
                <button className="th-btn" onClick={() => onSort(c)}>
                  {labelOf(query, c)}
                  <IconSort dir={sort?.col === c ? sort.dir : 0} className={sort?.col === c ? "sort-on" : "sort-off"} />
                </button>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              {cols.map((c) => {
                const f = fmtOf(c);
                return <td key={c} className={NUMERIC.has(query.columns[c]) ? "num" : ""} title={String(r[c] ?? "")}>{fmt(r[c], f)}</td>;
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
      <div className="table-foot">
        {data.truncated ? `First ${data.rows.length.toLocaleString()} of ${data.total_rows.toLocaleString()} rows · row cap` : `${data.rows.length.toLocaleString()} rows`}
      </div>
    </div>
  );
}
