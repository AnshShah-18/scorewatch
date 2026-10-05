-- Raw layer: BigQuery external tables reading the gzipped files straight from
-- Cloud Storage. Everything is STRING so nothing fails on odd codes ('RA', 'XX').
-- Layout: Freddie Mac SFLLD Release 47 (July 2026) -- 31 origination fields,
-- 35 performance fields. Typing and cleaning happen in 02_typed_tables.sql.

CREATE OR REPLACE EXTERNAL TABLE `__PROJECT__.scorewatch_raw.orig_ext` (
  credit_score STRING,                    -- 1
  first_payment_date STRING,              -- 2
  first_time_homebuyer_flag STRING,       -- 3
  maturity_date STRING,                   -- 4
  msa STRING,                             -- 5
  mi_pct STRING,                          -- 6
  num_units STRING,                       -- 7
  occupancy_status STRING,                -- 8
  orig_cltv STRING,                       -- 9
  orig_dti STRING,                        -- 10
  orig_upb STRING,                        -- 11
  orig_ltv STRING,                        -- 12
  orig_interest_rate STRING,              -- 13
  channel STRING,                         -- 14
  ppm_flag STRING,                        -- 15
  amortization_type STRING,               -- 16
  property_state STRING,                  -- 17
  property_type STRING,                   -- 18
  postal_code STRING,                     -- 19
  loan_sequence_number STRING,            -- 20
  loan_purpose STRING,                    -- 21
  orig_loan_term STRING,                  -- 22
  num_borrowers STRING,                   -- 23
  seller_name STRING,                     -- 24
  super_conforming_flag STRING,           -- 25
  pre_relief_loan_sequence_number STRING, -- 26
  special_eligibility_program STRING,     -- 27
  relief_refinance_indicator STRING,      -- 28
  property_valuation_method STRING,       -- 29
  interest_only_indicator STRING,         -- 30
  vantage_score4 STRING                   -- 31 (new in Release 47; 9999 = n/a)
)
OPTIONS (
  format = 'CSV',
  field_delimiter = '|',
  quote = '',
  compression = 'GZIP',
  uris = ['gs://__BUCKET__/raw/orig/*.txt.gz']
);

CREATE OR REPLACE EXTERNAL TABLE `__PROJECT__.scorewatch_raw.perf_ext` (
  loan_sequence_number STRING,            -- 1
  monthly_reporting_period STRING,        -- 2
  current_actual_upb STRING,              -- 3
  current_loan_delinquency_status STRING, -- 4
  loan_age STRING,                        -- 5
  remaining_months_to_maturity STRING,    -- 6
  defect_settlement_date STRING,          -- 7
  modification_flag STRING,               -- 8
  zero_balance_code STRING,               -- 9
  zero_balance_effective_date STRING,     -- 10
  current_interest_rate STRING,           -- 11
  current_non_interest_bearing_upb STRING,-- 12
  ddlpi STRING,                           -- 13 due date of last paid installment
  mi_recoveries STRING,                   -- 14
  net_sale_proceeds STRING,               -- 15
  non_mi_recoveries STRING,               -- 16
  total_expenses STRING,                  -- 17
  legal_costs STRING,                     -- 18
  maintenance_preservation_costs STRING,  -- 19
  taxes_insurance STRING,                 -- 20
  misc_expenses STRING,                   -- 21
  actual_loss STRING,                     -- 22
  cumulative_modification_cost STRING,    -- 23
  interest_rate_step_indicator STRING,    -- 24
  payment_deferral STRING,                -- 25
  estimated_ltv STRING,                   -- 26
  zero_balance_removal_upb STRING,        -- 27
  delinquent_accrued_interest STRING,     -- 28
  delinquency_due_to_disaster STRING,     -- 29
  borrower_assistance_status_code STRING, -- 30
  current_month_modification_cost STRING, -- 31
  interest_bearing_upb STRING,            -- 32
  mi_cancellation_indicator STRING,       -- 33 (moved from origination)
  servicer_name STRING,                   -- 34 (moved from origination)
  bankruptcy_cramdown_costs STRING        -- 35 (new in Release 47)
)
OPTIONS (
  format = 'CSV',
  field_delimiter = '|',
  quote = '',
  compression = 'GZIP',
  uris = ['gs://__BUCKET__/raw/perf/*.txt.gz']
);
