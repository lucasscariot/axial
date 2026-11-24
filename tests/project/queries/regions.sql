-- The choices of the Region filter: regions with bookings in [start_date, end_date).
SELECT region
FROM `analytics.fct_bookings`
WHERE DATE(created_at) >= @start_date
  AND DATE(created_at) < @end_date
  AND region IS NOT NULL
GROUP BY region
ORDER BY region
