-- A rate must be computed over the whole period, never averaged from daily rates.
SELECT
  COUNTIF(status = 'cancelled') AS cancelled,
  COUNT(*) AS bookings,
  SAFE_DIVIDE(COUNTIF(status = 'cancelled'), COUNT(*)) AS cancellation_rate
{{ bookings_in_range }}
