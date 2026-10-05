-- Dashboard layer: small, pre-aggregated tables (all well under the 50,000-row
-- Connected Sheets extract limit) that feed Google Sheets and Tableau Public.
-- Run after the Python scorecard steps.

-- 1. One row per origination year: target, model performance, stability --------
CREATE OR REPLACE TABLE `__PROJECT__.scorewatch.dash_vintage` AS
WITH outcomes AS (
  SELECT
    orig_year,
    COUNT(*) AS loans,
    COUNTIF(target_bad_24m IS NOT NULL) AS labeled_loans,
    COUNTIF(target_bad_24m = 1) AS bads,
    AVG(target_bad_24m) AS bad_rate,
    AVG(target_bad_24m_raw) AS bad_rate_unadjusted,
    AVG(IF(any_forbearance, 1, 0)) AS pct_ever_forbearance,
    COALESCE(
      MAX(CASE WHEN sample_role IN ('train', 'test') THEN '1 Development (2005-15)'
               WHEN sample_role = 'oot' THEN '2 Out-of-time (2016-19)'
               WHEN sample_role = 'covid_holdout' THEN '3 COVID holdout (2020-21)'
               WHEN sample_role = 'oot_recent' THEN '4 Recent out-of-time (2022+)' END),
      '5 Monitoring only') AS sample_group
  FROM `__PROJECT__.scorewatch.loan_outcomes`
  GROUP BY orig_year
),
metrics AS (
  SELECT CAST(segment AS INT64) AS orig_year, gini, ks, auc, gini_credit_score_only,
         gini - gini_credit_score_only AS gini_lift_vs_credit_score
  FROM `__PROJECT__.scorewatch.validation_metrics`
  WHERE level = 'orig_year'
),
scored AS (
  SELECT s.orig_year, AVG(s.score) AS avg_score, AVG(s.credit_score) AS avg_credit_score,
         AVG(s.pd) AS predicted_pd_ttc, AVG(m.pd_pit) AS predicted_pd_pit
  FROM `__PROJECT__.scorewatch.scores` s
  LEFT JOIN `__PROJECT__.scorewatch.score_to_pd` m USING (score)
  GROUP BY s.orig_year
)
SELECT
  o.*,
  m.* EXCEPT (orig_year),
  sc.* EXCEPT (orig_year),
  p.psi AS score_psi,
  p.status AS score_psi_status
FROM outcomes o
LEFT JOIN metrics m USING (orig_year)
LEFT JOIN scored sc USING (orig_year)
LEFT JOIN `__PROJECT__.scorewatch.psi_by_vintage` p USING (orig_year)
ORDER BY orig_year;


-- 2. Score bands by sample: rank ordering + calibration traffic lights ----------
CREATE OR REPLACE TABLE `__PROJECT__.scorewatch.dash_score_bands` AS
SELECT
  b.sample_role,
  CASE b.sample_role
    WHEN 'train' THEN '1 Train' WHEN 'test' THEN '2 Test'
    WHEN 'oot' THEN '3 Out-of-time 2016-19' WHEN 'covid_holdout' THEN '4 COVID 2020-21'
    WHEN 'oot_recent' THEN '5 Recent 2022+' ELSE '6 Monitoring' END AS sample_label,
  b.band,
  b.score_range,
  b.loans,
  b.pct_of_role,
  b.bads,
  b.bad_rate,
  b.mean_pd AS predicted_pd_ttc,
  c.predicted_pd_pit,
  c.z AS calibration_z,
  c.traffic_light
FROM `__PROJECT__.scorewatch.score_bands` b
LEFT JOIN `__PROJECT__.scorewatch.calibration_bands` c USING (sample_role, band);


-- 3. Score distribution in 10-point buckets by sample ----------------------------
CREATE OR REPLACE TABLE `__PROJECT__.scorewatch.dash_score_distribution` AS
SELECT
  sample_role,
  CAST(FLOOR(score / 10) * 10 AS INT64) AS score_bucket,
  COUNT(*) AS loans,
  COUNTIF(target_bad_24m = 1) AS bads,
  AVG(target_bad_24m) AS bad_rate
FROM `__PROJECT__.scorewatch.scores`
GROUP BY sample_role, score_bucket;


-- 4. Characteristic stability (CSI) by year, with each feature's strength ------
CREATE OR REPLACE TABLE `__PROJECT__.scorewatch.dash_csi` AS
SELECT c.feature, c.orig_year, c.csi, c.status, f.iv
FROM `__PROJECT__.scorewatch.csi_by_vintage` c
LEFT JOIN `__PROJECT__.scorewatch.feature_iv` f USING (feature);


-- 5. The scorecard itself: points per attribute ---------------------------------
CREATE OR REPLACE TABLE `__PROJECT__.scorewatch.dash_scorecard` AS
SELECT
  s.feature, s.bin, s.n AS train_loans, s.pct_pop, s.bad_rate, s.woe, s.points,
  f.iv AS feature_iv, f.strength, f.median_yearly_csi, m.coef AS model_coefficient
FROM `__PROJECT__.scorewatch.scorecard` s
LEFT JOIN `__PROJECT__.scorewatch.feature_iv` f USING (feature)
LEFT JOIN `__PROJECT__.scorewatch.model_coefficients` m ON m.term = s.feature;


-- 6. Geography: bad rate and score by state and period ----------------------------
CREATE OR REPLACE TABLE `__PROJECT__.scorewatch.dash_state` AS
SELECT
  s.property_state,
  CASE WHEN s.orig_year <= 2015 THEN '2005-2015'
       WHEN s.orig_year <= 2019 THEN '2016-2019'
       WHEN s.orig_year <= 2021 THEN '2020-2021'
       ELSE '2022-2026' END AS period,
  COUNT(*) AS loans,
  COUNTIF(s.target_bad_24m IS NOT NULL) AS labeled_loans,
  COUNTIF(s.target_bad_24m = 1) AS bads,
  AVG(s.target_bad_24m) AS bad_rate,
  AVG(s.score) AS avg_score,
  AVG(m.pd_pit) AS avg_predicted_pd_pit
FROM `__PROJECT__.scorewatch.scores` s
LEFT JOIN `__PROJECT__.scorewatch.score_to_pd` m USING (score)
WHERE s.property_state IS NOT NULL
GROUP BY property_state, period;


SELECT 'dash_vintage' AS table_name, COUNT(*) AS row_count FROM `__PROJECT__.scorewatch.dash_vintage`
UNION ALL SELECT 'dash_score_bands', COUNT(*) FROM `__PROJECT__.scorewatch.dash_score_bands`
UNION ALL SELECT 'dash_score_distribution', COUNT(*) FROM `__PROJECT__.scorewatch.dash_score_distribution`
UNION ALL SELECT 'dash_csi', COUNT(*) FROM `__PROJECT__.scorewatch.dash_csi`
UNION ALL SELECT 'dash_scorecard', COUNT(*) FROM `__PROJECT__.scorewatch.dash_scorecard`
UNION ALL SELECT 'dash_state', COUNT(*) FROM `__PROJECT__.scorewatch.dash_state`
ORDER BY table_name;
