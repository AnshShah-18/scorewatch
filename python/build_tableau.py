"""Generate the ScoreWatch Tableau workbook (dashboard/ScoreWatch.twb).

Reads the dashboard CSVs exported from BigQuery (dashboard/data/*.csv), reshapes
them into one tidy CSV per chart (dashboard/data/viz_*.csv) and writes a Tableau
workbook with eight worksheets and two dashboards. Open the .twb in Tableau
Public Desktop and use File > Save to Tableau Public.
"""
from __future__ import annotations

import sys
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "dashboard" / "data"
TWB = ROOT / "dashboard" / "ScoreWatch.twb"

BLUE, GRAY, ORANGE = "#1f4e79", "#b0b7bf", "#e07b39"
GREEN, AMBER, RED = "#4e9a52", "#e8a33d", "#c8423b"
ROLE_COLORS = ["#1f4e79", "#6a9fcf", "#e07b39", "#9b59b6", "#4e9a52"]


FEATURE_NAMES = {
    "credit_score": "Credit score", "orig_dti": "Debt-to-income", "orig_cltv": "Combined LTV",
    "orig_ltv": "LTV", "rate_spread": "Rate spread vs. market", "term_bucket": "Loan term",
    "num_borrowers": "Number of borrowers", "mi_pct": "Mortgage insurance",
    "channel": "Origination channel", "loan_purpose": "Loan purpose",
    "property_type": "Property type", "occupancy_status": "Occupancy",
    "orig_interest_rate": "Interest rate (excluded)", "orig_upb": "Loan amount",
}
CAPTIONS = {
    "orig_year": "Origination year", "bad_rate": "24-month bad rate", "series": "Series",
    "gini": "Gini", "model": "Model", "sample_group": "Sample", "score_psi": "Score PSI",
    "status": "Status", "sample": "Sample", "band": "Score decile", "score_range": "Score range",
    "loans": "Loans", "pct_of_role": "Share of sample", "traffic_light": "Calibration test",
    "predicted_pd_pit": "Predicted PD", "feature": "Characteristic", "csi": "CSI",
    "bin": "Attribute", "points": "Points", "pct_pop": "Share of training loans",
    "feature_iv": "Information value", "feature_rank": "IV rank", "property_state": "State",
    "period": "Period", "labeled_loans": "Labeled loans", "avg_score": "Average score",
    "year": "Origination year", "points_range": "Points at stake", "min_points": "Min points", "max_points": "Max points",
}


# --------------------------------------------------------------------------
# 1. Reshape exported tables into one tidy CSV per chart
# --------------------------------------------------------------------------
def build_viz_tables(data_dir: Path) -> dict[str, pd.DataFrame]:
    v = pd.read_csv(data_dir / "vintage.csv")
    bands = pd.read_csv(data_dir / "score_bands.csv")
    csi = pd.read_csv(data_dir / "csi.csv")
    card = pd.read_csv(data_dir / "scorecard.csv")
    state = pd.read_csv(data_dir / "state.csv")

    out = {}
    lab = v[v["bad_rate"].notna()]
    out["target_story"] = pd.concat([
        pd.DataFrame({"orig_year": lab["orig_year"], "series": "Adjusted (forbearance excluded)",
                      "bad_rate": lab["bad_rate"]}),
        pd.DataFrame({"orig_year": lab["orig_year"], "series": "Unadjusted (raw 90+ days past due)",
                      "bad_rate": lab["bad_rate_unadjusted"]}),
    ])
    g = v[v["gini"].notna()]
    out["gini_by_vintage"] = pd.concat([
        pd.DataFrame({"orig_year": g["orig_year"], "model": "ScoreWatch scorecard", "gini": g["gini"],
                      "sample_group": g["sample_group"].str[2:]}),
        pd.DataFrame({"orig_year": g["orig_year"], "model": "Credit score alone",
                      "gini": g["gini_credit_score_only"], "sample_group": g["sample_group"].str[2:]}),
    ])
    out["score_psi"] = pd.DataFrame({
        "orig_year": v["orig_year"], "score_psi": v["score_psi"],
        "status": v["score_psi_status"], "sample_group": v["sample_group"].str[2:]})

    b = bands[bands["bad_rate"].notna()].copy()
    b["sample"] = b["sample_label"].str[2:]
    out["rank_ordering"] = b[["sample", "band", "score_range", "loans", "pct_of_role", "bad_rate"]]

    c = bands[bands["traffic_light"].notna()].copy()
    c["sample"] = c["sample_label"].str[2:]
    c["traffic_light"] = c["traffic_light"].str.capitalize()
    out["calibration"] = c[["sample", "band", "traffic_light", "bad_rate", "predicted_pd_pit"]]

    s = csi.copy()
    s["status"] = s["status"].str.capitalize()
    s["feature"] = s["feature"].map(FEATURE_NAMES).fillna(s["feature"])
    s["year"] = "'" + (s["orig_year"] % 100).astype(str).str.zfill(2)
    out["csi"] = s[["feature", "orig_year", "year", "csi", "status"]]

    rng = card.groupby("feature").agg(min_points=("points", "min"), max_points=("points", "max"),
                                      feature_iv=("feature_iv", "first")).reset_index()
    rng["points_range"] = rng["max_points"] - rng["min_points"]
    rng["feature"] = rng["feature"].map(FEATURE_NAMES).fillna(rng["feature"])
    out["points_range"] = rng[["feature", "points_range", "min_points", "max_points", "feature_iv"]]

    sc = card.copy()
    sc["feature"] = sc["feature"].map(FEATURE_NAMES).fillna(sc["feature"])
    sc["feature_rank"] = sc["feature_iv"].rank(ascending=False, method="dense").astype(int)
    out["scorecard"] = sc[["feature", "bin", "points", "pct_pop", "bad_rate", "feature_iv",
                           "feature_rank"]]

    # Development era (2005-15) shows the crisis geography; small states/territories dropped
    st = state[(state["period"] == "2005-2015") & (state["labeled_loans"] >= 300)
               & state["bad_rate"].notna()]
    st = st.sort_values("bad_rate", ascending=False).head(20)
    out["state"] = st[["property_state", "period", "loans", "labeled_loans", "bad_rate", "avg_score"]]
    return out


# --------------------------------------------------------------------------
# 2. Workbook XML
# --------------------------------------------------------------------------
def tableau_type(s: pd.Series) -> str:
    if pd.api.types.is_integer_dtype(s):
        return "integer"
    if pd.api.types.is_float_dtype(s):
        return "real"
    return "string"


class DS:
    """One Tableau data source backed by one CSV."""

    def __init__(self, key: str, df: pd.DataFrame, data_dir: Path,
                 dims: set[str], geo: dict[str, str] | None = None,
                 formats: dict[str, str] | None = None,
                 palettes: dict[str, dict[str, str]] | None = None):
        self.key, self.df, self.dims, self.geo = key, df, dims, geo or {}
        self.formats, self.palettes = formats or {}, palettes or {}
        self.name = f"federated.{key}"
        self.conn = f"textscan.{key}"
        self.file = f"viz_{key}.csv"
        self.data_dir = data_dir
        self.types = {c: tableau_type(df[c]) for c in df.columns}

    def role(self, c: str) -> tuple[str, str]:
        if c in self.dims:
            return "dimension", ("ordinal" if self.types[c] == "integer" else "nominal")
        return "measure", "quantitative"

    def xml(self) -> str:
        cols = "\n".join(
            f"            <column datatype='{self.types[c]}' name={quoteattr(c)} ordinal='{i}' />"
            for i, c in enumerate(self.df.columns))
        remote = {"integer": 20, "real": 5, "string": 129}
        recs = "\n".join(f"""          <metadata-record class='column'>
            <remote-name>{escape(c)}</remote-name>
            <remote-type>{remote[self.types[c]]}</remote-type>
            <local-name>[{escape(c)}]</local-name>
            <parent-name>[{self.file}]</parent-name>
            <remote-alias>{escape(c)}</remote-alias>
            <ordinal>{i}</ordinal>
            <local-type>{self.types[c]}</local-type>
            <aggregation>{'Count' if self.types[c] == 'string' else 'Sum'}</aggregation>
            <contains-null>true</contains-null>
          </metadata-record>""" for i, c in enumerate(self.df.columns))
        fields = []
        for c in self.df.columns:
            role, typ = self.role(c)
            sem = f" semantic-role='{self.geo[c]}'" if c in self.geo else ""
            fmt_ = f" default-format='{self.formats[c]}'" if c in self.formats else ""
            cap = f" caption={quoteattr(CAPTIONS[c])}" if c in CAPTIONS else ""
            fields.append(f"      <column{cap} datatype='{self.types[c]}'{fmt_} name='[{c}]' role='{role}' "
                          f"type='{typ}'{sem} />")
        for c in self.palettes:
            role, typ = self.role(c)
            fields.append(f"      <column-instance column='[{c}]' derivation='None' "
                          f"name='{self.inst(c)}' pivot='key' type='{typ}' />")
        style = ""
        if self.palettes:
            rules = "".join(palette(self.inst(c), m) for c, m in self.palettes.items())
            style = f"\n      <style>\n        <style-rule element='mark'>{rules}</style-rule>\n      </style>"
        return f"""    <datasource caption='{self.key}' inline='true' name='{self.name}' version='18.1'>
      <connection class='federated'>
        <named-connections>
          <named-connection caption='{self.file}' name='{self.conn}'>
            <connection class='textscan' directory={quoteattr(str(self.data_dir))} filename='{self.file}' password='' server='' />
          </named-connection>
        </named-connections>
        <relation connection='{self.conn}' name='{self.file}' table='[{self.file.replace(".", "#")}]' type='table'>
          <columns character-set='UTF-8' header='yes' locale='en_US' separator=','>
{cols}
          </columns>
        </relation>
        <metadata-records>
{recs}
        </metadata-records>
      </connection>
      <aliases enabled='yes' />
{chr(10).join(fields)}{style}
    </datasource>"""

    # field references -----------------------------------------------------
    def inst(self, c: str, agg: str | None = None) -> str:
        """Column-instance name, e.g. [none:orig_year:ok] or [sum:bad_rate:qk]."""
        role, typ = self.role(c)
        if agg:
            return f"[{agg}:{c}:qk]"
        suffix = {"ordinal": "ok", "nominal": "nk", "quantitative": "qk"}[typ]
        return f"[none:{c}:{suffix}]"

    def ref(self, c: str, agg: str | None = None) -> str:
        return f"[{self.name}].{self.inst(c, agg)}"

    def deps(self, used: list[tuple[str, str | None]]) -> str:
        lines, seen = [], set()
        for c, agg in used:
            role, typ = self.role(c)
            if c not in seen:
                sem = f" semantic-role='{self.geo[c]}'" if c in self.geo else ""
                lines.append(f"            <column datatype='{self.types[c]}' name='[{c}]' "
                             f"role='{role}' type='{typ}'{sem} />")
                seen.add(c)
            deriv = {"sum": "Sum", "avg": "Avg", None: "None"}[agg]
            ityp = "quantitative" if agg else typ
            lines.append(f"            <column-instance column='[{c}]' derivation='{deriv}' "
                         f"name='{self.inst(c, agg)}' pivot='key' type='{ityp}' />")
        return (f"          <datasource-dependencies datasource='{self.name}'>\n"
                + "\n".join(dict.fromkeys(lines)) + "\n          </datasource-dependencies>")


def palette(field: str, mapping: dict[str, str]) -> str:
    maps = "".join(f"<map to='{col}'><bucket>&quot;{escape(k)}&quot;</bucket></map>"
                   for k, col in mapping.items())
    return f"<encoding attr='color' field='{field}' type='palette'>{maps}</encoding>"


def worksheet(name: str, *, title: str, ds: DS, rows: str, cols: str, mark: str,
              used: list[tuple[str, str | None]], encodings: str = "",
              style_rules: str = "", subtitle: str = "", mark_size: float | None = None,
              labels: bool = False, sort: str = "") -> str:
    pane_rules = []
    if mark_size:
        pane_rules.append(f"<format attr='size' value='{mark_size}' />")
    if labels:
        pane_rules.append("<format attr='mark-labels-show' value='true' />")
    pane_style = (f"\n            <style>\n              <style-rule element='mark'>{''.join(pane_rules)}"
                  f"</style-rule>\n            </style>") if pane_rules else ""
    sub = (f"<run fontcolor='#6b7280' fontsize='10'>{escape(subtitle)}</run>"
           if subtitle else "")
    br = "<run>Æ&#10;</run>" if subtitle else ""
    return f"""    <worksheet name={quoteattr(name)}>
      <layout-options>
        <title>
          <formatted-text><run bold='true' fontsize='13'>{escape(title)}</run>{br}{sub}</formatted-text>
        </title>
      </layout-options>
      <table>
        <view>
          <datasources>
            <datasource caption='{ds.key}' name='{ds.name}' />
          </datasources>
{ds.deps(used)}{sort}
          <aggregation value='true' />
        </view>
        <style>
{style_rules}
        </style>
        <panes>
          <pane selection-relaxation-option='selection-relaxation-allow'>
            <view>
              <breakdown value='auto' />
            </view>
            <mark class='{mark}' />
            <encodings>
{encodings}
            </encodings>{pane_style}
          </pane>
        </panes>
        <rows>{escape(rows)}</rows>
        <cols>{escape(cols)}</cols>
      </table>
      <simple-id uuid='{{{_uuid(name)}}}' />
    </worksheet>"""


def _uuid(seed: str) -> str:
    import uuid
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, "scorewatch." + seed)).upper()


def fmt(element: str, field: str, value: str) -> str:
    return (f"          <style-rule element='{element}'>\n"
            f"            <format attr='text-format' field='{field}' value='{value}' />\n"
            f"          </style-rule>")


def dashboard(name: str, heading: str, blurb: str, grid: list[list[str]], zid: list[int]) -> str:
    """Dashboard with a text header and a grid of worksheets placed at exact positions."""
    W, H = 1200, 900
    head_h = 8500                      # in 1/100000 of the dashboard height
    pad = 600
    row_h = (100000 - head_h) // len(grid)

    def nid():
        zid[0] += 1
        return zid[0]

    zones = [f"""          <zone h='{head_h}' id='{nid()}' type-v2='text' w='100000' x='0' y='0'>
            <formatted-text><run bold='true' fontsize='18'>{escape(heading)}</run><run>Æ&#10;</run><run fontcolor='#4b5563' fontsize='11'>{escape(blurb)}</run></formatted-text>
          </zone>"""]
    for r, sheets in enumerate(grid):
        w = 100000 // len(sheets)
        for i, sh in enumerate(sheets):
            zones.append(f"          <zone h='{row_h - pad}' id='{nid()}' name={quoteattr(sh)} "
                         f"w='{w - pad}' x='{i * w + pad // 2}' y='{head_h + r * row_h}' />")
    return f"""    <dashboard name={quoteattr(name)}>
      <style />
      <size maxheight='{H}' maxwidth='{W}' minheight='{H}' minwidth='{W}' />
      <zones>
        <zone h='100000' id='{nid()}' type-v2='layout-basic' w='100000' x='0' y='0'>
{chr(10).join(zones)}
        </zone>
      </zones>
      <simple-id uuid='{{{_uuid(name)}}}' />
    </dashboard>"""


def cards(ds_name: str | None = None, color_field: str | None = None) -> str:
    right = (f"\n        <edge name='right'>\n          <strip size='160'>\n"
             f"            <card pane-specification-id='0' param='{color_field}' type='color' />\n"
             f"          </strip>\n        </edge>") if color_field else ""
    return f"""      <cards>
        <edge name='left'>
          <strip size='160'>
            <card type='pages' />
            <card type='filters' />
            <card type='marks' />
          </strip>
        </edge>
        <edge name='top'>
          <strip size='2147483647'>
            <card type='columns' />
          </strip>
          <strip size='2147483647'>
            <card type='rows' />
          </strip>
          <strip size='31'>
            <card type='title' />
          </strip>
        </edge>{right}
      </cards>"""


def build_workbook(viz: dict[str, pd.DataFrame], data_dir: Path) -> str:
    PCT1, PCT2, DEC2 = "p0.0%", "p0.00%", "n#,##0.00"
    ds = {
        "target_story": DS("target_story", viz["target_story"], data_dir, {"orig_year", "series"},
                           formats={"bad_rate": PCT1},
                           palettes={"series": {"Adjusted (forbearance excluded)": BLUE,
                                                "Unadjusted (raw 90+ days past due)": ORANGE}}),
        "gini_by_vintage": DS("gini_by_vintage", viz["gini_by_vintage"], data_dir,
                              {"orig_year", "model", "sample_group"}, formats={"gini": DEC2},
                              palettes={"model": {"ScoreWatch scorecard": BLUE,
                                                  "Credit score alone": GRAY}}),
        "score_psi": DS("score_psi", viz["score_psi"], data_dir,
                        {"orig_year", "status", "sample_group"}, formats={"score_psi": DEC2},
                        palettes={"status": {"stable": GREEN, "monitor": AMBER,
                                             "significant shift": RED}}),
        "rank_ordering": DS("rank_ordering", viz["rank_ordering"], data_dir,
                            {"sample", "band", "score_range"}, formats={"bad_rate": PCT1},
                            palettes={"sample": dict(zip(
                                ["Train", "Test", "Out-of-time 2016-19", "COVID 2020-21",
                                 "Recent 2022+"], ROLE_COLORS))}),
        "calibration": DS("calibration", viz["calibration"], data_dir,
                          {"sample", "band", "traffic_light"},
                          formats={"bad_rate": PCT2, "predicted_pd_pit": PCT2},
                          palettes={"traffic_light": {"Green": GREEN, "Amber": AMBER, "Red": RED}}),
        "csi": DS("csi", viz["csi"], data_dir, {"feature", "orig_year", "year", "status"},
                  formats={"csi": DEC2},
                  palettes={"status": {"Stable": GREEN, "Monitor": AMBER,
                                       "Significant shift": RED}}),
        "scorecard": DS("scorecard", viz["scorecard"], data_dir, {"feature", "bin", "feature_rank"},
                        formats={"bad_rate": PCT2, "pct_pop": PCT1}),
        "points_range": DS("points_range", viz["points_range"], data_dir, {"feature"}),
        "state": DS("state", viz["state"], data_dir, {"property_state", "period"},
                    formats={"bad_rate": PCT1}),
    }
    sheets, wins = [], []

    def add(name, color=None, zoom="entire-view", **kw):
        sheets.append(worksheet(name, **kw))
        wins.append((name, color, zoom))

    d = ds["target_story"]
    add("Target story", color=d.ref("series"),
        title="24-month bad rate by origination year", ds=d,
        subtitle="Blue: forbearance excluded (model target). Orange: raw 90+ days past due. "
                 "86% of 2019 raw 'defaults' were COVID payment pauses.",
        rows=d.ref("bad_rate", "sum"), cols=d.ref("orig_year"), mark="Line",
        used=[("orig_year", None), ("series", None), ("bad_rate", "sum")],
        encodings=f"              <color column='{d.ref('series')}' />")

    d = ds["gini_by_vintage"]
    add("Gini by vintage", color=d.ref("model"),
        title="Gini by vintage: scorecard vs. credit score alone", ds=d,
        subtitle="Gini coefficient, higher is better. Blue: ScoreWatch. Gray: credit score alone. "
                 "Trained on 2005-15; 2016+ is out-of-time.",
        rows=d.ref("gini", "sum"), cols=d.ref("orig_year"), mark="Line",
        used=[("orig_year", None), ("model", None), ("gini", "sum")],
        encodings=f"              <color column='{d.ref('model')}' />")

    d = ds["rank_ordering"]
    add("Rank ordering", color=d.ref("sample"),
        title="Bad rate by score decile, all samples", ds=d,
        subtitle="B01 = riskiest 10%, B10 = safest. Dark blue train, light blue test, orange 2016-19, "
                 "purple COVID 2020-21, green 2022+.",
        rows=d.ref("bad_rate", "sum"), cols=d.ref("band"), mark="Line",
        used=[("band", None), ("sample", None), ("bad_rate", "sum")],
        encodings=f"              <color column='{d.ref('sample')}' />")

    d = ds["scorecard"]
    add("Scorecard", zoom="fit-width",
        title="The scorecard: points per attribute", ds=d,
        subtitle="Score = sum of points (600 = 50:1 good:bad odds; +20 points doubles the odds).",
        rows=f"({d.ref('feature')} / {d.ref('bin')})", cols="", mark="Text",
        used=[("feature", None), ("bin", None), ("points", "sum")],
        encodings=f"              <text column='{d.ref('points', 'sum')}' />")

    d = ds["points_range"]
    add("Points at stake",
        title="Points at stake per characteristic", ds=d,
        subtitle="Max minus min points each characteristic can contribute. Credit score moves a score "
                 "most; full points table is on the Scorecard tab.",
        rows=d.ref("feature"), cols=d.ref("points_range", "sum"), mark="Bar", labels=True,
        used=[("feature", None), ("points_range", "sum")],
        encodings=f"              <text column='{d.ref('points_range', 'sum')}' />",
        sort=(f"\n          <sort class='computed' column='{d.ref('feature')}' direction='DESC' "
              f"using='{d.ref('points_range', 'sum')}' />"))

    d = ds["score_psi"]
    add("Score stability",
        title="Score stability (PSI) by origination year", ds=d,
        subtitle="Vs. training population. Green < 0.10 stable, amber 0.10-0.25 monitor, red > 0.25 shift.",
        rows=d.ref("score_psi", "sum"), cols=d.ref("orig_year"), mark="Bar",
        used=[("orig_year", None), ("status", None), ("score_psi", "sum")],
        encodings=f"              <color column='{d.ref('status')}' />")

    d = ds["csi"]
    add("Characteristic drift",
        title="Characteristic drift (CSI) by year", ds=d,
        subtitle="Green stable, amber monitor, red shift. DTI shifts after 2019 (HARP-era missing DTI ends); "
                 "loan purpose shifts in 2023 (refinance collapse).",
        rows=d.ref("feature"), cols=d.ref("year"), mark="Square", mark_size=1.6,
        used=[("feature", None), ("year", None), ("status", None), ("csi", "sum")],
        encodings=(f"              <color column='{d.ref('status')}' />\n"
                   f"              <tooltip column='{d.ref('csi', 'sum')}' />"))

    d = ds["calibration"]
    add("Calibration",
        title="Calibration traffic lights by score decile", ds=d,
        subtitle="Binomial test per band (labels = actual bad rate). Fit on 2016-19. Red on 2022+ "
                 "high-risk bands: defaults rose among weaker borrowers.",
        rows=d.ref("band"), cols=d.ref("sample"), mark="Square", mark_size=3.0, labels=True,
        used=[("band", None), ("sample", None), ("traffic_light", None), ("bad_rate", "sum"),
              ("predicted_pd_pit", "sum")],
        encodings=(f"              <color column='{d.ref('traffic_light')}' />\n"
                   f"              <text column='{d.ref('bad_rate', 'sum')}' />\n"
                   f"              <tooltip column='{d.ref('predicted_pd_pit', 'sum')}' />"))

    d = ds["state"]
    add("State risk",
        title="Top 20 states by bad rate, 2005-15 loans", ds=d,
        subtitle="Crisis-era geography: Florida (4.3%) and Nevada (4.2%) lead. "
                 "States with at least 300 labeled loans.",
        rows=d.ref("property_state"), cols=d.ref("bad_rate", "sum"), mark="Bar", labels=True,
        used=[("property_state", None), ("bad_rate", "sum")],
        encodings=f"              <text column='{d.ref('bad_rate', 'sum')}' />",
        sort=(f"\n          <sort class='computed' column='{d.ref('property_state')}' direction='DESC' "
              f"using='{d.ref('bad_rate', 'sum')}' />"))

    zid = [0]
    dashes = [
        dashboard("Model overview", "ScoreWatch: credit risk scorecard on 1.06M Freddie Mac loans",
                  "BigQuery + Python WoE scorecard. Forbearance-adjusted 24-month default target, "
                  "10 stable characteristics, out-of-time validation 2016-2024.",
                  [["Target story", "Gini by vintage"], ["Rank ordering", "Points at stake"]], zid),
        dashboard("Monitoring", "ScoreWatch: model monitoring and calibration",
                  "Population stability, characteristic drift, calibrated PD vs. actual defaults, "
                  "and where losses concentrate.",
                  [["Score stability", "Calibration"], ["Characteristic drift", "State risk"]], zid),
    ]

    def win(cls, n, body, extra=""):
        return (f"    <window class='{cls}' {extra}name={quoteattr(n)}>\n{body}\n"
                f"      <simple-id uuid='{{{_uuid('win.' + n)}}}' />\n    </window>")

    windows = "\n".join(
        [win("dashboard", n,
             "      <viewpoints>\n" + "\n".join(
                 f"        <viewpoint name={quoteattr(v)}>\n          <zoom type='entire-view' />\n"
                 f"        </viewpoint>" for v in members) + "\n      </viewpoints>\n      <active id='-1' />",
             "maximized='true' ")
         for n, members in (("Model overview", ["Target story", "Gini by vintage", "Rank ordering", "Points at stake"]),
                            ("Monitoring", ["Score stability", "Calibration", "Characteristic drift", "State risk"]))]
        + [win("worksheet", n, cards(None, c) +
               f"\n      <viewpoint>\n        <zoom type='{z}' />\n      </viewpoint>")
           for n, c, z in wins])

    return f"""<?xml version='1.0' encoding='utf-8' ?>
<workbook original-version='18.1' source-build='2024.2.0 (20242.24.0613.1930)' source-platform='mac' version='18.1' xmlns:user='http://www.tableausoftware.com/xml/user'>
  <document-format-change-manifest>
    <MapboxVectorStylesAndLayers />
    <SheetIdentifierTracking />
    <WindowsPersistSimpleIdentifiers />
  </document-format-change-manifest>
  <datasources>
{chr(10).join(x.xml() for x in ds.values())}
  </datasources>
  <worksheets>
{chr(10).join(sheets)}
  </worksheets>
  <dashboards>
{chr(10).join(dashes)}
  </dashboards>
  <windows source-height='30'>
{windows}
  </windows>
</workbook>
"""


def main() -> None:
    data_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DATA
    target_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else data_dir
    viz = build_viz_tables(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    for k, df in viz.items():
        df.to_csv(data_dir / f"viz_{k}.csv", index=False)
        print(f"  viz_{k}.csv  {len(df):>4} rows")
    TWB.parent.mkdir(parents=True, exist_ok=True)
    TWB.write_text(build_workbook(viz, target_dir), encoding="utf-8")
    print(f"Wrote {TWB}")


if __name__ == "__main__":
    main()
