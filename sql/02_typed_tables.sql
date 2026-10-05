-- Staging layer: native, typed BigQuery tables. Freddie Mac's "not available"
-- codes (9, 99, 999, 9999) become NULL so they don't distort the model.

CREATE OR REPLACE TABLE `__PROJECT__.scorewatch.loans`
CLUSTER BY orig_year, property_state AS
SELECT
  TRIM(loan_sequence_number) AS loan_id,
  -- Loan IDs look like F05Q1xxxxxxx: product, 2-digit year, Q, quarter
  2000 + CAST(SUBSTR(TRIM(loan_sequence_number), 2, 2) AS INT64) AS orig_year,
  CAST(SUBSTR(TRIM(loan_sequence_number), 5, 1) AS INT64) AS orig_quarter,
  CASE WHEN SAFE_CAST(credit_score AS INT64) BETWEEN 300 AND 850
       THEN SAFE_CAST(credit_score AS INT64) END AS credit_score,
  CASE WHEN SAFE_CAST(vantage_score4 AS INT64) BETWEEN 300 AND 850
       THEN SAFE_CAST(vantage_score4 AS INT64) END AS vantage_score4,
  SAFE.PARSE_DATE('%Y%m', TRIM(first_payment_date)) AS first_payment_date,
  SAFE.PARSE_DATE('%Y%m', TRIM(maturity_date)) AS maturity_date,
  NULLIF(TRIM(first_time_homebuyer_flag), '9') AS first_time_homebuyer_flag,
  NULLIF(TRIM(msa), '') AS msa,
  SAFE_CAST(NULLIF(TRIM(mi_pct), '999') AS INT64) AS mi_pct,
  SAFE_CAST(NULLIF(TRIM(num_units), '99') AS INT64) AS num_units,
  NULLIF(TRIM(occupancy_status), '9') AS occupancy_status,
  SAFE_CAST(NULLIF(TRIM(orig_cltv), '999') AS INT64) AS orig_cltv,
  SAFE_CAST(NULLIF(TRIM(orig_dti), '999') AS INT64) AS orig_dti,
  SAFE_CAST(orig_upb AS NUMERIC) AS orig_upb,
  SAFE_CAST(NULLIF(TRIM(orig_ltv), '999') AS INT64) AS orig_ltv,
  SAFE_CAST(orig_interest_rate AS NUMERIC) AS orig_interest_rate,
  NULLIF(TRIM(channel), '9') AS channel,
  NULLIF(TRIM(ppm_flag), '') AS ppm_flag,
  NULLIF(TRIM(amortization_type), '') AS amortization_type,
  NULLIF(TRIM(property_state), '') AS property_state,
  NULLIF(TRIM(property_type), '99') AS property_type,
  NULLIF(TRIM(postal_code), '') AS postal_code_3digit,
  NULLIF(TRIM(loan_purpose), '9') AS loan_purpose,
  SAFE_CAST(orig_loan_term AS INT64) AS orig_loan_term,
  SAFE_CAST(NULLIF(TRIM(num_borrowers), '99') AS INT64) AS num_borrowers,
  NULLIF(TRIM(seller_name), '') AS seller_name,
  NULLIF(TRIM(super_conforming_flag), '') AS super_conforming_flag,
  NULLIF(TRIM(special_eligibility_program), '') AS special_eligibility_program,
  NULLIF(TRIM(relief_refinance_indicator), '') AS relief_refinance_indicator,
  NULLIF(TRIM(property_valuation_method), '7') AS property_valuation_method,
  NULLIF(TRIM(interest_only_indicator), '') AS interest_only_indicator
FROM `__PROJECT__.scorewatch_raw.orig_ext`;


CREATE OR REPLACE TABLE `__PROJECT__.scorewatch.performance`
PARTITION BY DATE_TRUNC(period_month, MONTH)
CLUSTER BY loan_id AS
SELECT
  TRIM(loan_sequence_number) AS loan_id,
  PARSE_DATE('%Y%m', TRIM(monthly_reporting_period)) AS period_month,
  SAFE_CAST(current_actual_upb AS NUMERIC) AS current_upb,
  TRIM(current_loan_delinquency_status) AS dq_status_raw,
  -- Numeric = months delinquent. 'RA' = REO acquisition (foreclosed property).
  -- 'XX' = status not yet reported (seen in first month of new loans) -> NULL.
  SAFE_CAST(TRIM(current_loan_delinquency_status) AS INT64) AS dq_months,
  TRIM(current_loan_delinquency_status) = 'RA' AS is_reo,
  SAFE_CAST(loan_age AS INT64) AS loan_age,
  SAFE_CAST(remaining_months_to_maturity AS INT64) AS remaining_months,
  NULLIF(TRIM(modification_flag), '') AS modification_flag,
  NULLIF(TRIM(zero_balance_code), '') AS zero_balance_code,
  SAFE.PARSE_DATE('%Y%m', NULLIF(TRIM(zero_balance_effective_date), '')) AS zero_balance_date,
  SAFE_CAST(current_interest_rate AS NUMERIC) AS current_interest_rate,
  SAFE_CAST(NULLIF(TRIM(estimated_ltv), '999') AS INT64) AS estimated_ltv,
  NULLIF(TRIM(payment_deferral), '') AS payment_deferral,
  NULLIF(TRIM(delinquency_due_to_disaster), '') AS dq_due_to_disaster,
  -- F = forbearance, R = repayment plan, T = trial modification. Key for 2020-21.
  NULLIF(TRIM(borrower_assistance_status_code), '') AS borrower_assistance_code,
  SAFE_CAST(actual_loss AS NUMERIC) AS actual_loss,
  NULLIF(TRIM(mi_cancellation_indicator), '7') AS mi_cancellation_indicator,
  NULLIF(TRIM(servicer_name), '') AS servicer_name
FROM `__PROJECT__.scorewatch_raw.perf_ext`;
