-- Quick checks after loading. Expect ~50k loans per full year (fewer for the
-- latest partial year), no NULL dates, and performance months up to ~mid 2026.

SELECT
  l.orig_year,
  COUNT(*) AS loans,
  ROUND(AVG(l.credit_score)) AS avg_fico,
  ROUND(100 * COUNTIF(l.credit_score IS NULL) / COUNT(*), 2) AS pct_fico_missing,
  ROUND(100 * COUNTIF(l.orig_dti IS NULL) / COUNT(*), 2) AS pct_dti_missing,
  ROUND(AVG(l.orig_interest_rate), 2) AS avg_rate
FROM `__PROJECT__.scorewatch.loans` l
GROUP BY 1
ORDER BY 1;

SELECT
  COUNT(*) AS perf_rows,
  COUNT(DISTINCT loan_id) AS loans_with_history,
  MIN(period_month) AS first_month,
  MAX(period_month) AS latest_month,
  COUNTIF(period_month IS NULL) AS null_months
FROM `__PROJECT__.scorewatch.performance`;
