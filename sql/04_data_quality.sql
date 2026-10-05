-- Data asset summary: field-level profile by vintage, category mix by vintage,
-- and rule-based integrity checks. These tables feed the data quality dashboard
-- and the monthly validation report.

-- 1. Numeric field profile by origination year ---------------------------------
CREATE OR REPLACE TABLE `__PROJECT__.scorewatch.dq_field_profile` AS
WITH long AS (
  SELECT orig_year, field, value
  FROM (
    SELECT
      orig_year,
      CAST(credit_score AS FLOAT64)       AS credit_score,
      CAST(vantage_score4 AS FLOAT64)     AS vantage_score4,
      CAST(orig_ltv AS FLOAT64)           AS orig_ltv,
      CAST(orig_cltv AS FLOAT64)          AS orig_cltv,
      CAST(orig_dti AS FLOAT64)           AS orig_dti,
      CAST(orig_upb AS FLOAT64)           AS orig_upb,
      CAST(orig_interest_rate AS FLOAT64) AS orig_interest_rate,
      CAST(mi_pct AS FLOAT64)             AS mi_pct,
      CAST(num_borrowers AS FLOAT64)      AS num_borrowers,
      CAST(num_units AS FLOAT64)          AS num_units,
      CAST(orig_loan_term AS FLOAT64)     AS orig_loan_term
    FROM `__PROJECT__.scorewatch.loans`
  )
  UNPIVOT INCLUDE NULLS (value FOR field IN (
    credit_score, vantage_score4, orig_ltv, orig_cltv, orig_dti, orig_upb,
    orig_interest_rate, mi_pct, num_borrowers, num_units, orig_loan_term))
)
SELECT
  orig_year,
  field,
  COUNT(*) AS n,
  COUNTIF(value IS NULL) AS n_missing,
  ROUND(100 * COUNTIF(value IS NULL) / COUNT(*), 2) AS pct_missing,
  MIN(value) AS min_value,
  APPROX_QUANTILES(value, 100)[SAFE_OFFSET(1)]  AS p01,
  APPROX_QUANTILES(value, 100)[SAFE_OFFSET(50)] AS p50,
  APPROX_QUANTILES(value, 100)[SAFE_OFFSET(99)] AS p99,
  MAX(value) AS max_value,
  ROUND(AVG(value), 3) AS mean_value
FROM long
GROUP BY orig_year, field;


-- 2. Categorical mix by origination year ---------------------------------------
CREATE OR REPLACE TABLE `__PROJECT__.scorewatch.dq_category_profile` AS
WITH long AS (
  SELECT orig_year, field, COALESCE(value, '<MISSING>') AS value
  FROM (
    SELECT orig_year, channel, occupancy_status, loan_purpose, property_type,
           first_time_homebuyer_flag, special_eligibility_program,
           relief_refinance_indicator, property_valuation_method
    FROM `__PROJECT__.scorewatch.loans`
  )
  UNPIVOT INCLUDE NULLS (value FOR field IN (
    channel, occupancy_status, loan_purpose, property_type,
    first_time_homebuyer_flag, special_eligibility_program,
    relief_refinance_indicator, property_valuation_method))
)
SELECT
  orig_year, field, value,
  COUNT(*) AS n,
  ROUND(100 * COUNT(*) / SUM(COUNT(*)) OVER (PARTITION BY orig_year, field), 2) AS pct
FROM long
GROUP BY orig_year, field, value;


-- 3. Integrity rules ------------------------------------------------------------
CREATE OR REPLACE TABLE `__PROJECT__.scorewatch.dq_rules` AS
WITH
  l AS (SELECT * FROM `__PROJECT__.scorewatch.loans`),
  perf_ids AS (SELECT DISTINCT loan_id FROM `__PROJECT__.scorewatch.performance`),
  perf_dupes AS (
    SELECT COUNT(*) AS n FROM (
      SELECT loan_id, period_month FROM `__PROJECT__.scorewatch.performance`
      GROUP BY 1, 2 HAVING COUNT(*) > 1)),
  perf_stats AS (
    SELECT
      COUNT(*) AS n_rows,
      COUNTIF(dq_status_raw = 'XX') AS n_dq_unreported,
      COUNTIF(dq_months IS NULL AND NOT is_reo AND dq_status_raw != 'XX') AS n_dq_unparsed,
      COUNTIF(loan_age < 0) AS n_negative_age
    FROM `__PROJECT__.scorewatch.performance`)
SELECT * FROM UNNEST([
  STRUCT(
    'L01' AS rule_id, 'loans' AS table_name,
    'Duplicate loan_id' AS rule,
    (SELECT COUNT(*) - COUNT(DISTINCT loan_id) FROM l) AS failing,
    (SELECT COUNT(*) FROM l) AS checked),
  STRUCT('L02', 'loans', 'Loan has no monthly performance history',
    (SELECT COUNT(*) FROM l LEFT JOIN perf_ids p USING (loan_id) WHERE p.loan_id IS NULL),
    (SELECT COUNT(*) FROM l)),
  STRUCT('L03', 'loans', 'Credit score missing or outside 300-850',
    (SELECT COUNTIF(credit_score IS NULL) FROM l), (SELECT COUNT(*) FROM l)),
  STRUCT('L04', 'loans', 'DTI missing',
    (SELECT COUNTIF(orig_dti IS NULL) FROM l), (SELECT COUNT(*) FROM l)),
  STRUCT('L05', 'loans', 'DTI above 65% (outside documented range)',
    (SELECT COUNTIF(orig_dti > 65) FROM l), (SELECT COUNTIF(orig_dti IS NOT NULL) FROM l)),
  STRUCT('L06', 'loans', 'Combined LTV lower than LTV',
    (SELECT COUNTIF(orig_cltv < orig_ltv) FROM l),
    (SELECT COUNTIF(orig_cltv IS NOT NULL AND orig_ltv IS NOT NULL) FROM l)),
  STRUCT('L07', 'loans', 'Interest rate <= 0 or > 15%',
    (SELECT COUNTIF(orig_interest_rate <= 0 OR orig_interest_rate > 15) FROM l), (SELECT COUNT(*) FROM l)),
  STRUCT('L08', 'loans', 'First payment date missing',
    (SELECT COUNTIF(first_payment_date IS NULL) FROM l), (SELECT COUNT(*) FROM l)),
  STRUCT('L09', 'loans', 'Maturity date before first payment date',
    (SELECT COUNTIF(maturity_date < first_payment_date) FROM l), (SELECT COUNT(*) FROM l)),
  STRUCT('P01', 'performance', 'Duplicate loan_id + reporting month',
    (SELECT n FROM perf_dupes), (SELECT n_rows FROM perf_stats)),
  STRUCT('P02', 'performance', 'Performance rows with no matching origination record',
    (SELECT COUNT(*) FROM perf_ids p LEFT JOIN l USING (loan_id) WHERE l.loan_id IS NULL),
    (SELECT COUNT(*) FROM perf_ids)),
  STRUCT('P03', 'performance', 'Delinquency status not yet reported (XX)',
    (SELECT n_dq_unreported FROM perf_stats), (SELECT n_rows FROM perf_stats)),
  STRUCT('P04', 'performance', 'Delinquency status unparseable (not numeric, RA or XX)',
    (SELECT n_dq_unparsed FROM perf_stats), (SELECT n_rows FROM perf_stats)),
  STRUCT('P05', 'performance', 'Negative loan age',
    (SELECT n_negative_age FROM perf_stats), (SELECT n_rows FROM perf_stats))
])
;

SELECT rule_id, rule, failing, checked,
       ROUND(100 * SAFE_DIVIDE(failing, checked), 3) AS pct_failing
FROM `__PROJECT__.scorewatch.dq_rules`
ORDER BY rule_id;

-- Is missing DTI explained by relief refinance (HARP) loans?
SELECT
  orig_year,
  COUNTIF(orig_dti IS NULL) AS dti_missing,
  COUNTIF(orig_dti IS NULL AND relief_refinance_indicator = 'Y') AS missing_and_relief_refi,
  ROUND(100 * SAFE_DIVIDE(
    COUNTIF(orig_dti IS NULL AND relief_refinance_indicator = 'Y'),
    COUNTIF(orig_dti IS NULL)), 1) AS pct_of_missing_explained
FROM `__PROJECT__.scorewatch.loans`
WHERE orig_year BETWEEN 2008 AND 2016
GROUP BY orig_year
ORDER BY orig_year;
