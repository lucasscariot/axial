-- One row per day in [start_date, end_date). The KPI and trend tiles share this result.
SELECT
  DATE(created_at) AS day,
  COUNT(*) AS bookings,
  SUM(gross_value) AS gross_value
{{ bookings_in_range }}
GROUP BY day
ORDER BY day
