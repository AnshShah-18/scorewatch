-- Target definition and sample design.
--
-- BAD (target = 1): within the first 24 months after first payment, the loan is
--   90+ days delinquent while NOT in forbearance and NOT flagged as disaster-
--   related, becomes REO, or terminates through a credit event (zero balance
--   code 02 third-party sale, 03 short sale, 09 REO, 15 note sale).
--   Delinquency during forbearance (e.g. CARES Act 2020-21, hurricane relief)
--   is a sanctioned payment pause, not a credit failure, so it is excluded.
-- GOOD (target = 0): full 24-month window observed with no bad event. Loans that
--   paid off early in a mature vintage are good.
-- UNLABELED (target = NULL): 24-month window not yet complete. Labeling these
--   early would bias bad rates upward (only early defaults/payoffs get labels).
--
-- Loan age 0 is the month before first payment, so months 0-24 cover the
-- first 24 scheduled payments.

DECLARE latest_month DATE DEFAULT (
  SELECT MAX(period_month) FROM `__PROJECT__.scorewatch.performance`);

CREATE OR REPLACE TABLE `__PROJECT__.scorewatch.loan_outcomes`
CLUSTER BY sample_role, orig_year AS
WITH window_events AS (
  SELECT
    loan_id,
    MAX(loan_age) AS max_age_observed,
    LOGICAL_OR(dq_months >= 3) AS ever_90dpd_raw,
    LOGICAL_OR(dq_months >= 3
               AND COALESCE(borrower_assistance_code, '') != 'F'
               AND COALESCE(dq_due_to_disaster, '') != 'Y') AS ever_90dpd,
    LOGICAL_OR(is_reo) AS ever_reo,
    LOGICAL_OR(zero_balance_code IN ('02', '03', '09', '15')) AS credit_termination,
    LOGICAL_OR(zero_balance_code IS NOT NULL) AS left_pool,
    LOGICAL_OR(zero_balance_code = '01') AS prepaid,
    LOGICAL_OR(borrower_assistance_code = 'F') AS any_forbearance,
    LOGICAL_OR(dq_months >= 3 AND borrower_assistance_code = 'F') AS dq90_while_in_forbearance,
    LOGICAL_OR(dq_due_to_disaster = 'Y') AS any_disaster_dq,
    LOGICAL_OR(modification_flag IN ('Y', 'P')) AS any_modification,
    MIN(IF((dq_months >= 3
            AND COALESCE(borrower_assistance_code, '') != 'F'
            AND COALESCE(dq_due_to_disaster, '') != 'Y')
           OR is_reo OR zero_balance_code IN ('02', '03', '09', '15'),
           loan_age, NULL)) AS months_to_default
  FROM `__PROJECT__.scorewatch.performance`
  WHERE loan_age BETWEEN 0 AND 24
  GROUP BY loan_id
),
labeled AS (
  SELECT
    l.loan_id,
    l.orig_year,
    l.first_payment_date,
    w.max_age_observed,
    DATE_ADD(l.first_payment_date, INTERVAL 23 MONTH) <= latest_month AS window_complete,
    COALESCE(w.ever_90dpd OR w.ever_reo OR w.credit_termination, FALSE) AS bad_event,
    COALESCE(w.ever_90dpd_raw OR w.ever_reo OR w.credit_termination, FALSE) AS bad_event_raw,
    w.* EXCEPT (loan_id, max_age_observed)
  FROM `__PROJECT__.scorewatch.loans` l
  LEFT JOIN window_events w USING (loan_id)
)
SELECT
  * EXCEPT (bad_event, bad_event_raw),
  CASE WHEN window_complete THEN IF(bad_event, 1, 0) END AS target_bad_24m,
  -- Unadjusted definition, kept to quantify the forbearance adjustment
  CASE WHEN window_complete THEN IF(bad_event_raw, 1, 0) END AS target_bad_24m_raw,
  CASE
    WHEN NOT COALESCE(window_complete, FALSE)
      THEN 'monitor'            -- outcome not yet known: drift / PSI only
    WHEN orig_year BETWEEN 2020 AND 2021
      THEN 'covid_holdout'      -- forbearance-distorted; analyzed separately
    WHEN orig_year BETWEEN 2016 AND 2019
      THEN 'oot'                -- out-of-time validation
    WHEN orig_year >= 2022
      THEN 'oot_recent'         -- recent out-of-time validation
    WHEN MOD(ABS(FARM_FINGERPRINT(loan_id)), 10) < 7
      THEN 'train'              -- 2005-2015, 70%
    ELSE 'test'                 -- 2005-2015, 30%
  END AS sample_role,
  latest_month AS as_of_month
FROM labeled;


-- Default rate by vintage
SELECT
  orig_year,
  STRING_AGG(DISTINCT sample_role ORDER BY sample_role) AS roles,
  COUNT(*) AS loans,
  COUNTIF(target_bad_24m IS NOT NULL) AS labeled,
  COUNTIF(target_bad_24m = 1) AS bads,
  ROUND(100 * AVG(target_bad_24m_raw), 3) AS bad_rate_raw_pct,
  ROUND(100 * AVG(target_bad_24m), 3) AS bad_rate_pct,
  ROUND(100 * AVG(IF(any_forbearance, 1, 0)), 2) AS pct_ever_forbearance
FROM `__PROJECT__.scorewatch.loan_outcomes`
GROUP BY orig_year
ORDER BY orig_year;

-- Sample design summary
SELECT
  sample_role,
  COUNT(*) AS loans,
  COUNTIF(target_bad_24m = 1) AS bads,
  ROUND(100 * AVG(target_bad_24m), 3) AS bad_rate_pct,
  MIN(orig_year) AS from_year,
  MAX(orig_year) AS to_year
FROM `__PROJECT__.scorewatch.loan_outcomes`
GROUP BY sample_role
ORDER BY from_year, sample_role;
