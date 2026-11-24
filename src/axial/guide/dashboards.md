# Dashboards

A dashboard is `dashboards/<id>.yaml`: an optional date filter, optional select filters and a
12-column grid of tiles.
Tiles fill rows left to right. The engine owns spacing, colours, fonts and formatting; you
choose tiles, sizes and order.

```yaml
title: Booking operations
description: optional
date_range: {default_days: 7, max_days: 31}   # binds start_date / end_date params
filters:                                       # optional dropdowns, see Filters
  - {param: region, label: Region, query: regions, column: region}
tiles:
  - {type: text, markdown: "Excludes test accounts. Owner: ops-analytics.", span: 12}
  - {type: kpi, query: bookings_daily, value: bookings, reduce: sum, span: 3}
  - {type: kpi, query: cancellation_rate, value: cancellation_rate, title: Cancellation rate, span: 3}
  - {type: line, query: bookings_daily, x: day, y: bookings, span: 9}
  - {type: bar, query: bookings_by_region, x: region, y: bookings, span: 3}
  - {type: table, query: booking_exceptions, span: 12, height: 400}
```

## Tile types

| type | required | optional |
|---|---|---|
| `kpi` | `query`, `value` (numeric column) | `reduce`: `single` (default; the query returns 1 row), `sum`, `min`, `max`, `first`, `last`, `count`; `format` |
| `line` | `query`, `x`, `y` (numeric column or list) | `series` (a column to split by, with a single `y`), `format`, `facet`, `facet_columns`, `lanes`, `highlight`, `step` (see [Time series](#time-series)) |
| `bar` | same as `line` | same as `line`, except `step` |
| `funnel` | `query`, `x` (stage column), `y` (one numeric column) | `format` |
| `table` | `query` | `columns` (subset and order), `formats` (per column) |
| `text` | `markdown` (headings, lists, **bold**, *italic*, `code`, https links) | |

Every tile has a `type` (above) and also takes `span` (1–12, default 12), `title`, and `height` (px; defaults are
kpi 64, charts 200, funnel 260, table 330). With `facet`, `height` is the height of each
panel (default 140, or 100 per lane with `lanes`); a `lanes` tile without `facet` defaults to
110 per lane.

The dashboard itself takes `title`, `description`, `date_range`, `filters` and `tiles`.
`axial check` rejects any other key, on the dashboard, in `date_range`, on a filter or on a
tile (for example `reduce` on a `line` tile, or a misspelt `height`).

Dashboards are the `.yaml` files directly inside `dashboards/`. A `.yml` file or a file in a
subfolder is not loaded, and `axial check` reports it.

## Layout guidance

- Put the headline KPIs on the first row, 3–4 per row (`span: 3` or `4`).
- Put trends (`line`) wide (`span: 8–12`) and breakdowns (`bar`) narrower.
- A `funnel` draws one row per stage, in the order the query returns them, so end the SQL
  with an `ORDER BY` on the step. Rows that repeat a stage are summed. Each stage shows its
  share of the first stage, and the tooltip adds the share of the previous one.
- Put detail tables last, at `span: 12`.
- Tiles that use the same query share one dataset and one BigQuery job, so reuse a query
  instead of writing near-duplicates.
- There is no `avg` reducer. Averages and rates belong in SQL.
- `line`/`bar` with `series` shows at most 8 series and folds the rest into "Other".
- For many entities, prefer `facet` (small multiples) at `span: 12` with `facet_columns: 2`–`4`
  over one chart with many series. Two measures in different units go in `lanes`, never on
  one axis.

## Time series

A `line` or `bar` tile whose `x` column is `TIMESTAMP` or `DATETIME` plots each row at its
instant on a time axis, so irregular readings keep their real spacing. A `TIMESTAMP` is
shown in the viewer's local time, as tables show it; a `DATETIME` is wall-clock time and is
shown exactly as written. With a date filter the axis spans the selected days, from the start
of the first to the end of the last; otherwise it fits the data. A `DATE` or any other `x`
keeps evenly spaced categories. Tooltips show the date and time.

Five optional keys make review dashboards: many entities, measures in different units, and the
periods where something held.

```yaml
- type: line
  query: service_readings      # service, read_at TIMESTAMP, error_rate, replicas, incident BOOL
  x: read_at
  y: [error_rate, replicas]
  facet: service               # one panel per service
  facet_columns: 2
  lanes: true                  # error rate and replicas each get a lane with their own scale
  highlight: incident          # shade the readings where incident is true
  step: replicas               # replicas are set and held, not measured
  height: 200                  # per panel
```

- `facet: <column>` draws one small panel per distinct value, titled with it, in the order
  the rows arrive, so end the SQL with an `ORDER BY` on that column (and on `x`). Panels share
  the x extent and, per measure, the y scale, so they compare at a glance; one legend serves
  the tile and hovering one panel shows the same instant in every panel. A tile draws at most
  50 panels and says how many it left out. `facet_columns` (1–4, default 1) sets the
  columns; narrow screens use fewer. `facet` works with `series`: every panel splits by
  `series`, with the same colour per value across panels and at most 8 series over the tile.
- `lanes: true` with a list `y` gives each column its own lane, stacked under the previous one
  on the same x axis, with its own y scale, label and format (the column's format, unless
  the tile sets `format`). Use it for measures in different units; there is no second y
  axis. It cannot be combined with `series`. Without it, a list `y` draws every column on
  one axis.
- `highlight: <BOOL column>` shades each run of consecutive points where the column is true,
  from its first to its last point, behind the data in every lane, with a thin edge at its
  start and end. A run of one point stays visible. The legend names it with the column's label and can hide it, and the tooltip says
  when a point is inside a run. Runs are found per panel; with `series`, a point counts as
  highlighted when any series is true at that x.
- `step: true` (every `y`), `step: <y column>` or `step: [<y columns>]`, on `line` tiles only,
  draws those lines as steps: each value holds until the next point. Use it for values that
  are set and changed, like a limit or a replica count, not for measurements.

These keys also work with a `DATE` or other category `x`.

## The date filter

- `date_range` binds to each query's `start_date` and `end_date` params. Use
  `start_param`/`end_param` to rename them.
- Every query on the dashboard must declare both params, or neither.
- Any other params a query declares need a default.
- The filter offers calendar periods, not rolling day counts, so everyone reading a number
  means the same days: **Today**, **This week**, **Last week**, **Month to date**,
  **Last month**, **Quarter to date** and **Year to date**. Weeks start on Monday, "to date"
  includes today, and "last" periods are whole. A dashboard shows the periods that always
  fit within `max_days` (7 for the weeks, 31 for the months, 92 for the quarter, 366 for the
  year). Any other range can be typed into the two date fields.
- `default` opens the dashboard on one of them: `today`, `this_week`, `last_week`,
  `month_to_date`, `last_month`, `quarter_to_date` or `year_to_date`. For example
  `date_range: {default: month_to_date, max_days: 31}`.
- `default_days: 7` opens it on the last 7 days including today instead. Use one or the other.
- The arrows on each side of the date fields move to the previous or next period of the same
  kind. A range of whole calendar months moves by months (Last month goes to the month
  before, a quarter to the quarter before). A period to date (This week, Month to date,
  Quarter to date, Year to date) steps back to the whole period before it, so This week goes
  to Last week. Any other range moves by its own length in days. Next never goes past today:
  it is disabled once the range includes today, and a step that would run past today ends
  today, so Next from Last week gives This week. Type dates in the fields to look further
  ahead, for example on a dashboard of forecasts.

## Filters

`filters` adds dropdowns to the toolbar, next to the date filter. Each one sets a query param
to one value, and its choices come from a saved query:

```yaml
filters:
  - {param: region, label: Region, query: regions, column: region}
  - {param: channel, query: channels, column: channel}     # label defaults to "Channel"
```

- `param` is the query param the filter sets. `label` names the dropdown (default: the
  param, humanized). `query` is the saved query that lists the choices, and `column` the
  STRING column they are read from: its distinct non-null values, in the order the rows
  arrive, so end that SQL with an `ORDER BY`.
- The first choice is **All**, which sends no value, so each query's default for the param
  applies. Declare the param as `{type: STRING, default: null}` and write
  `(@region IS NULL OR region = @region)` so All keeps every row (see `axial guide queries`).
- The filter applies to every tile query that declares the param, and is ignored by the
  others. `axial check` warns about the queries that ignore it, and rejects a filter whose
  param no tile query declares, is not STRING or has no default.
- The choices query is an ordinary saved query (same checks, cache, limits and history). It
  may take the date params, so the choices follow the selected dates; it cannot take any
  filter's param, so filters are independent: picking one never changes the choices of
  another. It does not need a tile.
- One value at a time, from the list only. The value reaches the warehouse as a query param,
  never as SQL text.
- The selection is kept in the page URL (`?region=North`), so a reload or a shared link
  keeps it, and it is printed under the title and in the PDF's name. A selected value that
  is no longer among the choices, because the dates changed, stays selected and is marked
  "not in these dates" ("not listed" when the choices do not depend on the dates): the tiles
  keep showing what the dropdown says until you pick another value or All.
- Each filter needs its own param, which cannot be a date param or `start`, `end` or
  `print` (the page URL uses those).
- With `--fixture`, the choices come from the choices query's fixture or made-up rows, and
  picking one does not filter the fixture data, just as fixtures ignore the dates.
  `axial preview <dashboard> --filter region=North` opens or renders a dashboard with a value
  selected.
