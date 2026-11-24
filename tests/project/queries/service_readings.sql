-- One row per service per reading, irregularly spaced. Ordered by service so the panels are too.
SELECT
  service,
  read_at,
  error_rate,
  latency,
  replicas,
  incident
FROM `metrics.service_readings`
WHERE DATE(read_at) >= @start_date
  AND DATE(read_at) < @end_date
ORDER BY service, read_at
