FROM `analytics.fct_bookings`
WHERE DATE(created_at) >= @start_date
  AND DATE(created_at) < @end_date
