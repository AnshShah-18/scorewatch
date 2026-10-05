"""Calibrate scorecard odds to a recent observation period.

The scorecard's built-in odds come from 2005-2015 (including the crisis), so its
PDs are a through-the-cycle (TTC) view. Lenders keep the score for rank-ordering
and fit a separate mapping from score to point-in-time (PIT) PD:

    logit(PD_pit) = a + b * logit(PD_ttc)

a shifts the overall default level, b lets the spread between low and high
scores change. Fitted on the calibration window, then checked out of time on the
recent sample with a binomial test per score band (traffic light: green |z|<1.96,
amber |z|<2.58, red otherwise).

Outputs (BigQuery + outputs/*.csv): calibration_summary, calibration_bands, score_to_pd
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

import common as C
from logit import fit_logit

CALIBRATION_ROLE = "oot"          # 2016-2019: most recent mature, pre-COVID window
EVALUATION_ROLES = ["oot", "covid_holdout", "oot_recent"]


def logit(p: pd.Series) -> pd.Series:
    p = p.clip(1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def traffic_light(z: float) -> str:
    if np.isnan(z):
        return "n/a"
    return "green" if abs(z) < 1.96 else ("amber" if abs(z) < 2.58 else "red")


def main() -> None:
    s = C.read_table("scores")
    spec = json.loads((C.OUTPUTS / "scorecard_spec.json").read_text())

    cal = s[(s["sample_role"] == CALIBRATION_ROLE) & s["target_bad_24m"].notna()]
    X = pd.DataFrame({"logit_pd_ttc": logit(cal["pd"])})
    fit = fit_logit(X, cal["target_bad_24m"].astype(int))
    a, b = float(fit.loc["const", "coef"]), float(fit.loc["logit_pd_ttc", "coef"])
    print(f"Calibration on '{CALIBRATION_ROLE}' ({len(cal):,} loans): "
          f"logit(PD_pit) = {a:+.4f} + {b:.4f} * logit(PD_ttc)")

    s["pd_pit"] = 1 / (1 + np.exp(-(a + b * logit(s["pd"]))))

    # Overall: expected vs observed by sample --------------------------------
    rows = []
    for role, d in s[s["target_bad_24m"].notna()].groupby("sample_role"):
        n, bads = len(d), int(d["target_bad_24m"].sum())
        for kind, col in (("ttc", "pd"), ("pit", "pd_pit")):
            exp = d[col].sum()
            var = (d[col] * (1 - d[col])).sum()
            z = (bads - exp) / np.sqrt(var) if var > 0 else np.nan
            rows.append({"sample_role": role, "pd_type": kind, "loans": n, "bads": bads,
                         "actual_bad_rate": bads / n, "predicted_bad_rate": exp / n,
                         "ratio_actual_to_predicted": bads / exp if exp else np.nan,
                         "z": z, "traffic_light": traffic_light(z)})
    summary = pd.DataFrame(rows)
    print("\nPredicted vs actual bad rate:")
    print(summary.round(4).to_string(index=False))

    # By score band on each evaluation sample ---------------------------------
    train = s[s["sample_role"] == "train"]
    cuts = np.unique(np.quantile(train["score"], np.linspace(0, 1, 11)[1:-1]))
    edges = [-np.inf, *cuts, np.inf]
    s["band"] = pd.cut(s["score"], bins=edges,
                       labels=[f"B{i + 1:02d}" for i in range(len(edges) - 1)]).astype(str)
    band_rows = []
    for role in EVALUATION_ROLES:
        d = s[(s["sample_role"] == role) & s["target_bad_24m"].notna()]
        for band, g in d.groupby("band"):
            bads, exp = g["target_bad_24m"].sum(), g["pd_pit"].sum()
            var = (g["pd_pit"] * (1 - g["pd_pit"])).sum()
            z = (bads - exp) / np.sqrt(var) if var > 0 else np.nan
            band_rows.append({"sample_role": role, "band": band, "loans": len(g),
                              "bads": int(bads), "actual_bad_rate": bads / len(g),
                              "predicted_pd_pit": exp / len(g),
                              "predicted_pd_ttc": g["pd"].mean(),
                              "z": z, "traffic_light": traffic_light(z)})
    bands = pd.DataFrame(band_rows)
    print("\nCalibrated PD vs actual by band (traffic lights):")
    print(bands.pivot(index="band", columns="sample_role", values="traffic_light")
               .reindex(columns=EVALUATION_ROLES).to_string())

    # Score-to-PD lookup -----------------------------------------------------
    scores = np.arange(int(s["score"].min()), int(s["score"].max()) + 1)
    pd_ttc = 1 / (1 + np.exp((scores - spec["offset"]) / spec["factor"]))
    lookup = pd.DataFrame({"score": scores, "pd_ttc": pd_ttc,
                           "pd_pit": 1 / (1 + np.exp(-(a + b * logit(pd.Series(pd_ttc)))))})

    print("\nWriting outputs")
    C.write_table(summary, "calibration_summary")
    C.write_table(bands, "calibration_bands")
    C.write_table(lookup, "score_to_pd")
    spec["calibration"] = {"window": CALIBRATION_ROLE, "a": a, "b": b}
    (C.OUTPUTS / "scorecard_spec.json").write_text(json.dumps(spec, indent=2))
    print("Done.")


if __name__ == "__main__":
    main()
