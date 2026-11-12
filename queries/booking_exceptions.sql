SELECT
  created_at,
  booking_id,
  exception_type,
  region,
  wait_seconds,
  gross_value
FROM `analytics.fct_booking_exceptions`
WHERE DATE(created_at) >= @start_date
  AND DATE(created_at) < @end_date
ORDER BY created_at DESC
LIMIT @limit
