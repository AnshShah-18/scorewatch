# ScoreWatch

Credit risk scorecard development, validation, and monitoring on Freddie Mac
Single-Family Loan-Level data (2005–2026), built on Google Cloud.

## Architecture

```
Freddie Mac sample zips
   │  scripts/01_prepare_data.sh   unzip, verify layout, gzip
   ▼
Cloud Storage  gs://<bucket>/raw/{orig,perf}/
   │  sql/01_external_tables.sql   raw layer, all STRING, no storage cost
   ▼
BigQuery scorewatch_raw.*_ext
   │  sql/02_typed_tables.sql      typed, NULL-coded, partitioned + clustered
   ▼
BigQuery scorewatch.loans / scorewatch.performance
   │  sql/04_data_quality.sql      field profiles, category mix, integrity rules
   │  sql/05_loan_outcomes.sql     24-month bad flag (forbearance-adjusted), sample roles
   │  sql/06_model_base.sql        origination-time features + rate spread
   ▼
python/train_scorecard.py          WoE binning, IV + correlation selection,
                                   logistic regression, points scaling (600 @ 50:1, PDO 20)
python/validate.py                 KS / Gini / calibration by sample and vintage,
                                   score bands, PSI and CSI by vintage
python/calibrate.py                score -> point-in-time PD mapping (fit on 2016-19),
                                   expected vs actual with binomial traffic lights
   ▼
BigQuery scorewatch.scores / scorecard / validation_metrics / score_bands /
         psi_by_vintage / csi_by_vintage / feature_iv / model_coefficients /
         calibration_summary / calibration_bands / score_to_pd
```

## Running it

```bash
# 1. Put sample_*.zip in data/raw/ (or point RAW at your folder)
RAW=~/path/to/Data bash scripts/01_prepare_data.sh

# 2. cp config.env.example config.env, fill it in, then authenticate once
gcloud auth login
bash scripts/02_load_to_gcp.sh
bash scripts/03_quality_and_labels.sh

# 3. Scorecard (Python client needs application-default credentials once)
gcloud auth application-default login
gcloud auth application-default set-quota-project $PROJECT_ID
bash scripts/04_build_scorecard.sh
```

## Model design decisions

- **Interest-rate level excluded** by an automated stability screen: its bin mix
  shifts so much between origination years (median yearly CSI 3.08) that it acts
  as a proxy for the economic cycle. `rate_spread` (rate vs same-quarter, same-term
  average) keeps the pricing signal and is stable (CSI 0.03).
- **Forbearance-adjusted target**: delinquency while in CARES Act / disaster
  forbearance is not counted as bad. Without this, 70-85% of 2018-19 "defaults"
  were COVID payment pauses.
- **Mature-window labeling only**: loans without a full 24-month window are
  unlabeled, avoiding the upward bias of early-default-only labels.
- **Separate calibration layer**: the score keeps its through-the-cycle odds for
  rank-ordering; a two-parameter logistic mapping converts it to point-in-time PD.

## Data notes

- Bad = 90+ DPD (outside forbearance / disaster relief), REO, or credit-event termination within 24 months.
- Loans with no complete 24-month outcome window are unlabeled and used only for monitoring.
- Sample design: train/test 2005-2015 (70/30), OOT 2016-2019, COVID holdout 2020-2021, recent OOT 2022+.
- 2020–2021 vintages are affected by COVID forbearance (`borrower_assistance_code = 'F'`).
- Layout follows SFLLD Release 47 (July 2026): 31 origination fields, 35 performance fields.
- Freddie Mac "not available" codes (9 / 99 / 999 / 9999) are converted to NULL.
