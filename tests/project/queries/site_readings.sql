-- Sensor readings per site in the site's local (civil) time.
SELECT
  site,
  read_at,
  temperature,
  pressure,
  heating
FROM `metrics.site_readings`
WHERE DATE(read_at) >= @start_date
  AND DATE(read_at) < @end_date
ORDER BY site, read_at
