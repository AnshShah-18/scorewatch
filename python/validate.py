"""Validate the ScoreWatch scorecard.

Discrimination   KS, AUC, Gini by sample role and by origination year
Calibration      predicted vs actual bad rate by sample role and score band
Rank ordering    bad rate by score band (bands = train score deciles)
Stability        PSI of the score distribution and CSI of every characteristic,
                 by origination year, against the train population
Benchmark        Gini of the scorecard vs credit score alone

Outputs (BigQuery + outputs/*.csv): validation_metrics, score_bands,
psi_by_vintage, csi_by_vintage, validation_summary.json
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

import common as C

EPS = 1e-4
ROLE_ORDER = ["train", "test", "oot", "covid_holdout", "oot_recent", "monitor"]


def roc_auc_score(y: np.ndarray, risk: np.ndarray) -> float:
    """AUC via the rank-sum (Mann-Whitney) formula, ties averaged."""
    y = np.asarray(y, dtype=int)
    ranks = pd.Series(np.asarray(risk, dtype=float)).rank(method="average").to_numpy()
    n1 = y.sum()
    n0 = len(y) - n1
    return float((ranks[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def ks_stat(y: np.ndarray, score: np.ndarray) -> float:
    """Max distance between the score CDFs of goods and bads."""
    order = np.argsort(score)
    y = y[order]
    cum_bad = np.cumsum(y) / max(y.sum(), 1)
    cum_good = np.cumsum(1 - y) / max((1 - y).sum(), 1)
    return float(np.max(np.abs(cum_bad - cum_good)))


def discrimination(d: pd.DataFrame) -> dict:
    y = d["target_bad_24m"].to_numpy(dtype=int)
    if y.sum() == 0 or y.sum() == len(y):
        return {}
    s = d["score"].to_numpy(dtype=float)
    auc = roc_auc_score(y, -s)                     # higher score = lower risk
    out = {"n": len(d), "bads": int(y.sum()), "bad_rate": float(y.mean()),
           "mean_pd": float(d["pd"].mean()), "auc": auc, "gini": 2 * auc - 1,
           "ks": ks_stat(y, s)}
    fico = d["credit_score"]
    m = fico.notna().to_numpy()
    if m.sum() and 0 < y[m].sum() < m.sum():
        out["gini_credit_score_only"] = 2 * roc_auc_score(y[m], -fico[m]) - 1
    return out


def psi(expected: pd.Series, actual: pd.Series) -> float:
    e = expected.reindex(expected.index.union(actual.index)).fillna(0).clip(lower=EPS)
    a = actual.reindex(e.index).fillna(0).clip(lower=EPS)
    return float(((a - e) * np.log(a / e)).sum())


def psi_flag(v: float) -> str:
    return "stable" if v < 0.10 else ("monitor" if v < 0.25 else "significant shift")


def main() -> None:
    s = C.read_table("scores")
    spec = json.loads((C.OUTPUTS / "scorecard_spec.json").read_text())
    features = spec["selected"]
    train = s[s["sample_role"] == "train"]
    labeled = s[s["target_bad_24m"].notna()]

    # Discrimination & calibration by role and by vintage ---------------------
    rows = []
    for role in ROLE_ORDER:
        d = labeled[labeled["sample_role"] == role]
        m = discrimination(d) if len(d) else {}
        if m:
            rows.append({"level": "sample_role", "segment": role, **m})
    for yr, d in labeled.groupby("orig_year"):
        m = discrimination(d)
        if m:
            rows.append({"level": "orig_year", "segment": str(int(yr)), **m})
    metrics = pd.DataFrame(rows)
    print("Discrimination and calibration:")
    print(metrics.round(4).to_string(index=False))

    # Score bands: deciles of the train score distribution --------------------
    cuts = np.unique(np.quantile(train["score"], np.linspace(0, 1, 11)[1:-1]))
    edges = [-np.inf, *cuts, np.inf]
    labels = [f"B{i + 1:02d}" for i in range(len(edges) - 1)]
    s["band"] = pd.cut(s["score"], bins=edges, labels=labels, right=True).astype(str)
    band_ranges = {lab: f"{'min' if np.isinf(lo) else int(lo) + 1}-{'max' if np.isinf(hi) else int(hi)}"
                   for lab, lo, hi in zip(labels, edges[:-1], edges[1:])}
    bands = (s.groupby(["sample_role", "band"])
              .agg(loans=("loan_id", "count"),
                   bads=("target_bad_24m", "sum"),
                   labeled=("target_bad_24m", "count"),
                   mean_pd=("pd", "mean"),
                   min_score=("score", "min"), max_score=("score", "max"))
              .reset_index())
    bands["bad_rate"] = bands["bads"] / bands["labeled"].replace(0, np.nan)
    bands["pct_of_role"] = bands["loans"] / bands.groupby("sample_role")["loans"].transform("sum")
    bands["score_range"] = bands["band"].map(band_ranges)
    print("\nBad rate by score band (B01 = riskiest):")
    print(bands.pivot(index="band", columns="sample_role", values="bad_rate")
               .reindex(columns=[r for r in ROLE_ORDER if r in bands["sample_role"].unique()])
               .round(4).to_string())

    # PSI of score by vintage ---------------------------------------------------
    base_dist = s.loc[train.index, "band"].value_counts(normalize=True)
    psi_rows = []
    for yr, d in s.groupby("orig_year"):
        v = psi(base_dist, d["band"].value_counts(normalize=True))
        psi_rows.append({"orig_year": int(yr), "loans": len(d),
                         "roles": ",".join(sorted(d["sample_role"].unique())),
                         "psi": v, "status": psi_flag(v)})
    psi_df = pd.DataFrame(psi_rows)
    print("\nScore PSI vs train population:")
    print(psi_df.round(4).to_string(index=False))

    # CSI of each characteristic by vintage -------------------------------------
    csi_rows = []
    for f in features:
        col = f"bin_{f}"
        e = s.loc[train.index, col].value_counts(normalize=True)
        for yr, d in s.groupby("orig_year"):
            v = psi(e, d[col].value_counts(normalize=True))
            csi_rows.append({"feature": f, "orig_year": int(yr), "csi": v, "status": psi_flag(v)})
    csi_df = pd.DataFrame(csi_rows)
    worst = csi_df.sort_values("csi", ascending=False).head(10)
    print("\nLargest characteristic shifts (CSI):")
    print(worst.round(4).to_string(index=False))

    # Save -------------------------------------------------------------------
    print("\nWriting outputs")
    C.write_table(metrics, "validation_metrics")
    C.write_table(bands, "score_bands")
    C.write_table(psi_df, "psi_by_vintage")
    C.write_table(csi_df, "csi_by_vintage")
    by_role = metrics[metrics["level"] == "sample_role"].set_index("segment")
    summary = {
        "as_of_vintages": f"{int(s['orig_year'].min())}-{int(s['orig_year'].max())}",
        "features": features,
        "roles": {r: {k: round(float(v), 4) for k, v in by_role.loc[r].items()
                      if k not in ("level",) and pd.notna(v)}
                  for r in by_role.index},
        "max_score_psi": round(float(psi_df["psi"].max()), 4),
        "vintages_with_score_shift": psi_df.loc[psi_df["psi"] >= 0.25, "orig_year"].tolist(),
    }
    (C.OUTPUTS / "validation_summary.json").write_text(json.dumps(summary, indent=2))
    print("Done.")


if __name__ == "__main__":
    main()
