"""Develop the ScoreWatch application scorecard.

Steps
  1. Bin every candidate characteristic on the TRAIN sample (WoE / IV).
  2. Keep characteristics with IV >= 0.02, drop one of any pair whose WoE values
     correlate above 0.7 (keeping the higher IV).
  3. Fit a logistic regression of bad on WoE. Any characteristic whose
     coefficient has the wrong sign (would reward risk) is dropped and the
     model refit; the same for p-values above 0.05.
  4. Scale to points (600 @ 50:1 odds, PDO 20) and score every loan.

Outputs (BigQuery + outputs/*.csv): feature_iv, scorecard, model_coefficients, scores
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

import common as C
from binning import bin_categorical, bin_numeric, iv_strength, psi
from logit import fit_logit, vif

IV_MIN = 0.02
CORR_MAX = 0.70
STABILITY_MAX = 1.0   # max median year-vs-train CSI within the training years
P_MAX = 0.05


def main() -> None:
    df = C.prepare(C.read_table("model_base"))
    print(f"Loaded {len(df):,} loans")
    train = df[df["sample_role"] == "train"].copy()
    y = train[C.TARGET].astype(int)
    print(f"Train: {len(train):,} loans, {int(y.sum()):,} bads ({y.mean():.2%})")

    # 1. Binning ---------------------------------------------------------------
    bins = {}
    for f in C.NUMERIC:
        bins[f] = bin_numeric(f, train[f], y)
    for f in C.CATEGORICAL:
        bins[f] = bin_categorical(f, train[f], y)

    iv = pd.DataFrame({
        "feature": list(bins),
        "type": [b.kind for b in bins.values()],
        "n_bins": [len(b.table) for b in bins.values()],
        "iv": [round(b.iv, 4) for b in bins.values()],
        "trend": [b.trend for b in bins.values()],
    }).sort_values("iv", ascending=False).reset_index(drop=True)
    iv["strength"] = iv["iv"].map(iv_strength)

    # Stability across origination years (train only). A characteristic whose
    # distribution swings from vintage to vintage is tracking the economic cycle
    # (e.g. the absolute interest rate level), not the borrower.
    stab = {}
    for f, b in bins.items():
        lab = b.labels(train[f])
        base = lab.value_counts(normalize=True)
        yearly = [psi(base, lab[train["orig_year"] == yr].value_counts(normalize=True))
                  for yr in sorted(train["orig_year"].unique())]
        stab[f] = float(np.median(yearly))
    iv["median_yearly_csi"] = iv["feature"].map(stab).round(3)

    # 2. Selection by IV and correlation ----------------------------------------
    woe_train = pd.DataFrame({f: bins[f].woe(train[f]) for f in bins}, index=train.index)
    candidates = iv.loc[(iv["iv"] >= IV_MIN) & (iv["median_yearly_csi"] <= STABILITY_MAX),
                        "feature"].tolist()
    corr = woe_train[candidates].corr().abs()
    selected, reasons = [], {}
    for f in candidates:                      # already sorted by IV, highest first
        clash = [s for s in selected if corr.loc[f, s] > CORR_MAX]
        if clash:
            reasons[f] = f"correlated with {clash[0]} (|r|={corr.loc[f, clash[0]]:.2f})"
        else:
            selected.append(f)
    for f in iv["feature"]:
        if f in candidates:
            continue
        if stab[f] > STABILITY_MAX:
            reasons[f] = f"unstable across vintages (median CSI {stab[f]:.2f}): economic-cycle proxy"
        else:
            reasons[f] = f"IV below {IV_MIN}"

    # 3. Logistic regression with sign / significance checks -------------------
    while True:
        fit = fit_logit(woe_train[selected], y)
        coefs = fit["coef"].drop("const")
        pvals = fit["p_value"].drop("const")
        wrong_sign = coefs[coefs > 0]          # with ln(good/bad) WoE, coefs must be < 0
        insignificant = pvals[pvals > P_MAX]
        if len(wrong_sign):
            f = wrong_sign.index[0]
            reasons[f] = f"wrong coefficient sign ({coefs[f]:+.3f}) after multivariate fit"
        elif len(insignificant):
            f = insignificant.idxmax()
            reasons[f] = f"not significant (p={pvals[f]:.3f})"
        else:
            break
        selected.remove(f)
        print(f"  dropping {f}: {reasons[f]}")

    iv["selected"] = iv["feature"].isin(selected)
    iv["reason"] = iv["feature"].map(lambda f: "in model" if f in selected else reasons.get(f, ""))
    print("\nCharacteristic analysis (train):")
    print(iv.to_string(index=False))

    coef_table = fit.rename_axis("term").reset_index()
    coef_table["iv"] = coef_table["term"].map(dict(zip(iv["feature"], iv["iv"])))
    coef_table["vif"] = coef_table["term"].map(vif(woe_train[selected]))
    print("\nModel coefficients:")
    print(coef_table.round(4).to_string(index=False))

    # 4. Points scaling -------------------------------------------------------
    factor = C.PDO / np.log(2)
    offset = C.BASE_SCORE - factor * np.log(C.BASE_ODDS)
    b0, k = fit.loc["const", "coef"], len(selected)
    rows = []
    for f in selected:
        t = bins[f].table.copy()
        t["points"] = (-(coefs[f] * t["woe"] + b0 / k) * factor + offset / k).round().astype(int)
        t.insert(0, "feature", f)
        rows.append(t)
    scorecard = pd.concat(rows, ignore_index=True)
    scorecard = scorecard[["feature", "bin", "n", "bads", "pct_pop", "bad_rate", "woe", "iv", "points"]]
    print("\nScorecard:")
    print(scorecard[["feature", "bin", "pct_pop", "bad_rate", "points"]].to_string(index=False))

    # Score everyone (train, test, OOT, COVID holdout, monitor)
    points = pd.DataFrame(index=df.index)
    for f in selected:
        lut = scorecard.loc[scorecard["feature"] == f].set_index("bin")["points"]
        lab = bins[f].labels(df[f])
        points[f] = lab.map(lut)
        # Unseen category: neutral points (the population-average bin)
        neutral = int(round((-(b0 / k)) * factor + offset / k))
        points[f] = points[f].fillna(neutral)
    score = points.sum(axis=1).astype(int)
    log_odds_good = (score - offset) / factor
    pd_hat = 1 / (1 + np.exp(log_odds_good))

    scores = pd.DataFrame({
        "loan_id": df["loan_id"], "orig_year": df["orig_year"],
        "orig_quarter": df["orig_quarter"], "sample_role": df["sample_role"],
        "target_bad_24m": df[C.TARGET], "score": score, "pd": pd_hat.round(6),
        "credit_score": df["credit_score"], "vantage_score4": df["vantage_score4"],
        "property_state": df["property_state"],
    })
    for f in selected:
        scores[f"pts_{f}"] = points[f].astype(int)
        scores[f"bin_{f}"] = bins[f].labels(df[f]).astype(str)

    # Save ---------------------------------------------------------------------
    print("\nWriting outputs")
    C.write_table(iv, "feature_iv")
    C.write_table(scorecard, "scorecard")
    C.write_table(coef_table, "model_coefficients")
    C.write_table(scores, "scores")
    spec = {
        "selected": selected, "base_score": C.BASE_SCORE, "base_odds": C.BASE_ODDS,
        "pdo": C.PDO, "factor": factor, "offset": offset,
        "intercept": float(b0), "coefficients": {f: float(coefs[f]) for f in selected},
        "bins": {f: {"kind": bins[f].kind, "edges": [None if np.isinf(e) else e for e in bins[f].edges],
                     "levels": bins[f].levels} for f in selected},
    }
    C.OUTPUTS.mkdir(parents=True, exist_ok=True)
    (C.OUTPUTS / "scorecard_spec.json").write_text(json.dumps(spec, indent=2))
    print(f"Done. Score range {score.min()}-{score.max()}, median {int(score.median())}")


if __name__ == "__main__":
    main()
