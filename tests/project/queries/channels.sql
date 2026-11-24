-- The choices of the Channel filter. It takes no dates, so they never change.
SELECT channel
FROM `analytics.dim_channels`
ORDER BY sort_order
