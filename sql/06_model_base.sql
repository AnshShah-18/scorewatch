-- Modeling table: one row per loan with origination-time characteristics only
-- (nothing observed after origination may enter the scorecard), plus target and
-- sample role from loan_outcomes.

CREATE OR REPLACE TABLE `__PROJECT__.scorewatch.model_base`
CLUSTER BY sample_role, orig_year AS
WITH base AS (
  SELECT
    l.*,
    CASE
      WHEN l.orig_loan_term <= 180 THEN 'TERM_15Y_OR_LESS'
      WHEN l.orig_loan_term <= 240 THEN 'TERM_16_20Y'
      ELSE 'TERM_OVER_20Y'
    END AS term_bucket
  FROM `__PROJECT__.scorewatch.loans` l
)
SELECT
  b.loan_id,
  b.orig_year,
  b.orig_quarter,
  o.sample_role,
  o.target_bad_24m,

  -- Numeric characteristics
  b.credit_score,
  b.orig_ltv,
  b.orig_cltv,
  b.orig_dti,
  b.orig_interest_rate,
  -- Spread at origination: borrower's rate vs the average rate for loans of the
  -- same term originated in the same quarter. Captures risk-based pricing the
  -- lender applied, independent of the interest-rate environment.
  ROUND(b.orig_interest_rate - AVG(b.orig_interest_rate) OVER (
    PARTITION BY b.orig_year, b.orig_quarter, b.term_bucket), 4) AS rate_spread,
  CAST(b.orig_upb AS FLOAT64) AS orig_upb,
  b.num_borrowers,
  b.mi_pct,

  -- Categorical characteristics
  b.occupancy_status,
  b.loan_purpose,
  b.channel,
  b.property_type,
  b.first_time_homebuyer_flag,
  b.term_bucket,
  CAST(b.num_units AS STRING) AS num_units,
  IF(b.relief_refinance_indicator = 'Y', 'Y', 'N') AS relief_refi_flag,
  IF(b.orig_cltv > b.orig_ltv, 'Y', 'N') AS has_secondary_financing,

  -- Kept for reporting and challenger comparisons, not used as model inputs
  b.vantage_score4,
  b.property_state
FROM base b
JOIN `__PROJECT__.scorewatch.loan_outcomes` o USING (loan_id);

SELECT sample_role, COUNT(*) AS loans, COUNTIF(target_bad_24m = 1) AS bads,
       ROUND(AVG(rate_spread), 4) AS avg_spread,
       ROUND(100 * COUNTIF(vantage_score4 IS NOT NULL) / COUNT(*), 2) AS pct_with_vantage
FROM `__PROJECT__.scorewatch.model_base`
GROUP BY sample_role
ORDER BY sample_role;
