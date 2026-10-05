"""Package the dashboards as dashboard/ScoreWatch.twbx with embedded Hyper extracts.

Tableau Public (Desktop Public Edition) only opens and publishes workbooks whose
data sources are extracts. This script writes one .hyper file per chart CSV with
the Tableau Hyper API and zips them, the CSVs and the workbook into a .twbx.

    .venv/bin/pip install tableauhyperapi
    .venv/bin/python python/package_twbx.py
"""
from __future__ import annotations

import shutil
import tempfile
import zipfile
from pathlib import Path

import pandas as pd
from tableauhyperapi import (Connection, CreateMode, HyperProcess, Inserter, SqlType,
                             TableDefinition, TableName, Telemetry)

import build_tableau as bt

TWBX = bt.ROOT / "dashboard" / "ScoreWatch.twbx"
SQL_TYPES = {"integer": SqlType.big_int(), "real": SqlType.double(), "string": SqlType.text()}


def write_hyper(df: pd.DataFrame, path: Path, hyper: HyperProcess) -> None:
    types = {c: bt.tableau_type(df[c]) for c in df.columns}
    table = TableDefinition(TableName("Extract", "Extract"),
                            [TableDefinition.Column(c, SQL_TYPES[types[c]]) for c in df.columns])
    with Connection(hyper.endpoint, path, CreateMode.CREATE_AND_REPLACE) as conn:
        conn.catalog.create_schema("Extract")
        conn.catalog.create_table(table)
        rows = []
        for rec in df.itertuples(index=False):
            row = []
            for c, v in zip(df.columns, rec):
                if pd.isna(v):
                    row.append(None)
                elif types[c] == "integer":
                    row.append(int(v))
                elif types[c] == "real":
                    row.append(float(v))
                else:
                    row.append(str(v))
            rows.append(row)
        with Inserter(conn, table) as ins:
            ins.add_rows(rows)
            ins.execute()


def main() -> None:
    viz = bt.build_viz_tables(bt.DATA)
    work = Path(tempfile.mkdtemp())
    (work / "Data" / "viz").mkdir(parents=True)
    (work / "Data" / "Extracts").mkdir(parents=True)

    with HyperProcess(Telemetry.DO_NOT_SEND_USAGE_DATA_TO_TABLEAU) as hyper:
        for key, df in viz.items():
            df.to_csv(work / "Data" / "viz" / f"viz_{key}.csv", index=False)
            write_hyper(df, work / "Data" / "Extracts" / f"{key}.hyper", hyper)
            print(f"  {key:<16} {len(df):>4} rows -> Data/Extracts/{key}.hyper")

    bt.EXTRACT_DIR = "Data/Extracts"
    (work / "ScoreWatch.twb").write_text(bt.build_workbook(viz, Path("Data/viz")), encoding="utf-8")

    with zipfile.ZipFile(TWBX, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(work.rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(work).as_posix())
    shutil.rmtree(work)
    print(f"Wrote {TWBX}")


if __name__ == "__main__":
    main()
