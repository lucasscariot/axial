-- Bookings per day and region. With no filter value, @region and @channel are NULL and keep every row.
SELECT
  DATE(created_at) AS day,
  region,
  COUNT(*) AS bookings,
  SUM(gross_value) AS gross_value
FROM `analytics.fct_bookings`
WHERE DATE(created_at) >= @start_date
  AND DATE(created_at) < @end_date
  AND (@region IS NULL OR region = @region)
  AND (@channel IS NULL OR channel = @channel)
GROUP BY day, region
ORDER BY day, region
