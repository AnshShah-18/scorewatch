"""Shared config and data access for ScoreWatch.

Reads PROJECT_ID / LOCATION from config.env. Set SCOREWATCH_LOCAL=<dir> to run
fully offline: tables are read from <dir>/<table>.pkl and written to
<dir>/out/<table>.csv instead of BigQuery (used for testing).
"""
from __future__ import annotations

import os
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

# Google client libraries warn about the Python 3.10 end-of-life on every import
warnings.filterwarnings("ignore", category=FutureWarning, module=r"google\..*")

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "cache"
OUTPUTS = ROOT / "outputs"


def _config() -> dict:
    cfg = {}
    path = ROOT / "config.env"
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip()
    return cfg


CFG = _config()
PROJECT = CFG.get("PROJECT_ID", "")
LOCATION = CFG.get("LOCATION", "US")
DATASET = "scorewatch"
LOCAL = os.environ.get("SCOREWATCH_LOCAL")


def _client():
    from google.cloud import bigquery
    return bigquery.Client(project=PROJECT, location=LOCATION)


def read_table(name: str, refresh: bool = False) -> pd.DataFrame:
    """Read a BigQuery table (cached as parquet after the first pull)."""
    if LOCAL:
        return pd.read_pickle(Path(LOCAL) / f"{name}.pkl")
    CACHE.mkdir(parents=True, exist_ok=True)
    cache = CACHE / f"{name}.parquet"
    if cache.exists() and not refresh:
        return pd.read_parquet(cache)
    print(f"Downloading {DATASET}.{name} from BigQuery ...")
    df = _client().query(f"SELECT * FROM `{PROJECT}.{DATASET}.{name}`").to_dataframe(
        create_bqstorage_client=True)
    df.to_parquet(cache, index=False)
    return df


def write_table(df: pd.DataFrame, name: str) -> None:
    """Replace a BigQuery table with df, and keep a CSV copy in outputs/."""
    OUTPUTS.mkdir(parents=True, exist_ok=True)
    if len(df) <= 200_000:
        df.to_csv(OUTPUTS / f"{name}.csv", index=False)
    # Keep a parquet copy so later steps don't re-download what we just wrote
    if LOCAL:
        df.to_pickle(Path(LOCAL) / f"{name}.pkl")
    else:
        CACHE.mkdir(parents=True, exist_ok=True)
        df.to_parquet(CACHE / f"{name}.parquet", index=False)
    if LOCAL:
        out = Path(LOCAL) / "out"
        out.mkdir(parents=True, exist_ok=True)
        df.to_csv(out / f"{name}.csv", index=False)
        print(f"  wrote {name} ({len(df):,} rows) [local]")
        return
    from google.cloud import bigquery
    job = _client().load_table_from_dataframe(
        df, f"{PROJECT}.{DATASET}.{name}",
        job_config=bigquery.LoadJobConfig(write_disposition="WRITE_TRUNCATE"))
    job.result()
    print(f"  wrote {DATASET}.{name} ({len(df):,} rows)")


# Model specification -------------------------------------------------------------
TARGET = "target_bad_24m"
NUMERIC = [
    "credit_score", "orig_ltv", "orig_cltv", "orig_dti", "orig_interest_rate",
    "rate_spread", "orig_upb", "num_borrowers", "mi_pct",
]
CATEGORICAL = [
    "occupancy_status", "loan_purpose", "channel", "property_type",
    "first_time_homebuyer_flag", "term_bucket", "num_units",
    "relief_refi_flag", "has_secondary_financing",
]

def prepare(df: pd.DataFrame) -> pd.DataFrame:
    """Feature engineering applied identically to every sample before binning."""
    df = df.copy()
    # Freddie Mac's 'T' (third party, not specified) code is used mostly in older
    # vintages, so the raw channel code partly encodes origination era.
    # Retail vs third-party origination is the stable, meaningful distinction.
    ch = df["channel"]
    df["channel"] = ch.where(ch.isna(), np.where(ch == "R", "RETAIL", "THIRD_PARTY"))
    return df


# Score scaling: 600 points at 50:1 good:bad odds, 20 points to double the odds.
BASE_SCORE = 600
BASE_ODDS = 50
PDO = 20
