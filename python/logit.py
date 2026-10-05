"""Maximum-likelihood logistic regression with standard errors (IRLS / Newton).

Small and dependency-free so the coefficient table (coef, std err, z, p-value,
VIF) needed for model documentation comes straight from numpy.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd


@np.errstate(all="ignore")  # macOS Accelerate BLAS emits spurious FP warnings in matmul
def fit_logit(X: pd.DataFrame, y: pd.Series, max_iter: int = 50, tol: float = 1e-8) -> pd.DataFrame:
    """Fit P(y=1) = 1 / (1 + exp(-(b0 + X b))). Returns a coefficient table
    indexed by term ('const' first) with coef, std_err, z, p_value."""
    Xm = np.column_stack([np.ones(len(X)), X.to_numpy(dtype=float)])
    yv = y.to_numpy(dtype=float)
    beta = np.zeros(Xm.shape[1])
    p0 = yv.mean()
    beta[0] = math.log(p0 / (1 - p0))
    for _ in range(max_iter):
        eta = Xm @ beta
        p = 1 / (1 + np.exp(-eta))
        w = p * (1 - p)
        grad = Xm.T @ (yv - p)
        hess = (Xm * w[:, None]).T @ Xm
        step = np.linalg.solve(hess, grad)
        beta += step
        if np.max(np.abs(step)) < tol:
            break
    p = 1 / (1 + np.exp(-(Xm @ beta)))
    cov = np.linalg.inv((Xm * (p * (1 - p))[:, None]).T @ Xm)
    se = np.sqrt(np.diag(cov))
    z = beta / se
    pval = np.array([math.erfc(abs(v) / math.sqrt(2)) for v in z])
    terms = ["const", *X.columns]
    return pd.DataFrame({"coef": beta, "std_err": se, "z": z, "p_value": pval}, index=terms)


@np.errstate(all="ignore")
def vif(X: pd.DataFrame) -> pd.Series:
    """Variance inflation factor of each column against the others."""
    A = X.to_numpy(dtype=float)
    out = {}
    for i, col in enumerate(X.columns):
        others = np.delete(A, i, axis=1)
        if others.shape[1] == 0:
            out[col] = 1.0
            continue
        M = np.column_stack([np.ones(len(A)), others])
        coef, *_ = np.linalg.lstsq(M, A[:, i], rcond=None)
        resid = A[:, i] - M @ coef
        ss_tot = ((A[:, i] - A[:, i].mean()) ** 2).sum()
        r2 = 1 - (resid ** 2).sum() / ss_tot if ss_tot > 0 else 0.0
        out[col] = 1 / max(1 - r2, 1e-12)
    return pd.Series(out)
