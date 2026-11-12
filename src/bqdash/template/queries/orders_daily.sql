-- One row per day in [start_date, end_date). end_date is exclusive.
SELECT
  DATE(created_at) AS day,
  COUNT(*) AS orders,
  SUM(amount) AS revenue
FROM `your_dataset.orders`
WHERE DATE(created_at) >= @start_date
  AND DATE(created_at) < @end_date
GROUP BY day
ORDER BY day
