"""Weight-of-evidence binning for scorecard development.

Numeric features: fine-class into quantile bins, then merge adjacent bins until
the bad rate is monotonic in the feature and every bin holds a minimum share of
the population. Missing values always get their own bin.
Categorical features: one bin per level, with rare levels pooled into OTHER.

WoE convention: woe = ln(%good / %bad). Higher WoE = lower risk.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

MISSING = "MISSING"
OTHER = "OTHER"
SMOOTH = 0.5      # added to good/bad counts so empty cells don't produce +/-inf
MIN_BIN_N = 500   # bins smaller than this get neutral WoE (too little data to trust)


@dataclass
class FeatureBins:
    name: str
    kind: str                                   # "numeric" | "categorical"
    edges: list[float] = field(default_factory=list)   # numeric upper edges (last = inf)
    levels: dict[str, str] = field(default_factory=dict)  # category -> bin label
    table: pd.DataFrame | None = None           # bin, n, bads, goods, bad_rate, woe, iv
    iv: float = 0.0
    trend: str = ""

    # -- mapping ----------------------------------------------------------------
    def labels(self, x: pd.Series) -> pd.Series:
        if self.kind == "numeric":
            out = pd.Series(MISSING, index=x.index, dtype=object)
            v = pd.to_numeric(x, errors="coerce")
            ok = v.notna()
            idx = np.searchsorted(np.asarray(self.edges[:-1]), v[ok].to_numpy(), side="left")
            names = np.array([self._numeric_label(i) for i in range(len(self.edges))], dtype=object)
            out[ok] = names[idx]
            return out
        s = x.astype(object).where(x.notna(), MISSING).astype(str)
        mapped = s.map(self.levels)
        fallback = OTHER if OTHER in self.levels.values() else None
        return mapped.fillna(fallback if fallback else "__UNSEEN__")

    def _numeric_label(self, i: int) -> str:
        lo = -np.inf if i == 0 else self.edges[i - 1]
        hi = self.edges[i]
        lo_s = "-inf" if np.isinf(lo) else f"{lo:g}"
        hi_s = "inf" if np.isinf(hi) else f"{hi:g}"
        return f"{i:02d}: ({lo_s}, {hi_s}]"

    def woe(self, x: pd.Series) -> pd.Series:
        lut = dict(zip(self.table["bin"], self.table["woe"]))
        return self.labels(x).map(lut).fillna(0.0).astype(float)


def _woe_table(labels: pd.Series, y: pd.Series, order: list[str] | None = None) -> pd.DataFrame:
    t = pd.DataFrame({"bin": labels.values, "y": y.values}).groupby("bin")["y"].agg(
        n="count", bads="sum")
    t["goods"] = t["n"] - t["bads"]
    G, B = t["goods"].sum(), t["bads"].sum()
    pg = (t["goods"] + SMOOTH) / (G + SMOOTH * len(t))
    pb = (t["bads"] + SMOOTH) / (B + SMOOTH * len(t))
    t["pct_pop"] = t["n"] / t["n"].sum()
    t["bad_rate"] = t["bads"] / t["n"]
    t["woe"] = np.where(t["n"] >= MIN_BIN_N, np.log(pg / pb), 0.0)
    t["iv"] = (pg - pb) * t["woe"]
    t = t.reset_index()
    if order:
        t["_o"] = t["bin"].map({b: i for i, b in enumerate(order)})
        t = t.sort_values("_o").drop(columns="_o")
    return t.reset_index(drop=True)


def bin_numeric(name: str, x: pd.Series, y: pd.Series, n_fine: int = 20,
                min_share: float = 0.05) -> FeatureBins:
    v = pd.to_numeric(x, errors="coerce")
    ok = v.notna()
    vx, vy = v[ok].to_numpy(dtype=float), y[ok].to_numpy(dtype=float)

    # 1. Fine classing on quantiles
    qs = np.unique(np.quantile(vx, np.linspace(0, 1, n_fine + 1)[1:-1]))
    edges = list(qs) + [np.inf]

    def stats(edges_):
        idx = np.searchsorted(np.asarray(edges_[:-1]), vx, side="left")
        n = np.bincount(idx, minlength=len(edges_)).astype(float)
        b = np.bincount(idx, weights=vy, minlength=len(edges_))
        return n, b

    n, b = stats(edges)
    keep = n > 0
    edges = [e for e, k in zip(edges, keep) if k]
    if edges[-1] != np.inf:
        edges[-1] = np.inf

    # 2. Direction of the relationship decides the monotonic constraint
    # Spearman = Pearson correlation of ranks (avoids a scipy dependency)
    corr = pd.Series(vx).rank().corr(pd.Series(vy).rank())
    increasing = bool(corr >= 0) if not np.isnan(corr) else True

    def merge(i):  # merge bin i with bin i+1
        del edges[i]

    # 3. Merge until monotonic
    while len(edges) > 1:
        n, b = stats(edges)
        rate = (b + SMOOTH) / (n + 2 * SMOOTH)
        diffs = np.diff(rate)
        bad_pairs = np.where(diffs < 0)[0] if increasing else np.where(diffs > 0)[0]
        if len(bad_pairs) == 0:
            break
        i = bad_pairs[np.argmin(np.abs(diffs[bad_pairs]))]
        merge(i)

    # 4. Merge undersized bins (or bins with no bads/goods) into closest neighbour
    total = len(vx)
    while len(edges) > 1:
        n, b = stats(edges)
        small = np.where((n < min_share * total) | (b == 0) | (b == n))[0]
        if len(small) == 0:
            break
        i = small[np.argmin(n[small])]
        rate = (b + SMOOTH) / (n + 2 * SMOOTH)
        if i == 0:
            merge(0)
        elif i == len(edges) - 1:
            merge(i - 1)
        else:
            merge(i - 1 if abs(rate[i] - rate[i - 1]) <= abs(rate[i] - rate[i + 1]) else i)

    fb = FeatureBins(name, "numeric", edges=edges,
                     trend="increasing" if increasing else "decreasing")
    labels = fb.labels(x)
    order = [fb._numeric_label(i) for i in range(len(edges))] + [MISSING]
    fb.table = _woe_table(labels, y, order)
    fb.iv = float(fb.table["iv"].sum())
    return fb


def bin_categorical(name: str, x: pd.Series, y: pd.Series,
                    min_share: float = 0.02) -> FeatureBins:
    s = x.astype(object).where(x.notna(), MISSING).astype(str)
    share = s.value_counts(normalize=True)
    levels = {lvl: (lvl if share[lvl] >= min_share or lvl == MISSING else OTHER)
              for lvl in share.index}
    # A lone OTHER bin that is still tiny is folded into the largest level
    other_share = share[[l for l, b in levels.items() if b == OTHER]].sum()
    if 0 < other_share < min_share:
        biggest = share.index[0]
        levels = {l: (biggest if b == OTHER else b) for l, b in levels.items()}
        levels[OTHER] = biggest
    fb = FeatureBins(name, "categorical", levels=levels)
    labels = s.map(levels)
    t = _woe_table(labels, y)
    fb.table = t.sort_values("bad_rate").reset_index(drop=True)
    fb.iv = float(fb.table["iv"].sum())
    return fb


def psi(expected: pd.Series, actual: pd.Series, eps: float = 1e-4) -> float:
    """Population stability index between two distributions (shares by bin)."""
    idx = expected.index.union(actual.index)
    e = expected.reindex(idx).fillna(0).clip(lower=eps)
    a = actual.reindex(idx).fillna(0).clip(lower=eps)
    return float(((a - e) * np.log(a / e)).sum())


def iv_strength(iv: float) -> str:
    if iv < 0.02:
        return "unpredictive"
    if iv < 0.1:
        return "weak"
    if iv < 0.3:
        return "medium"
    if iv < 0.5:
        return "strong"
    return "very strong"
