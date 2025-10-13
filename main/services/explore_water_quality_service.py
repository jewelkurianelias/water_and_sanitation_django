# main/services/explore_water_quality_service.py
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Dict, List, Tuple, Optional
import pandas as pd
import numpy as np
import yaml
from prophet import Prophet
from datetime import datetime

# -----------------------------
# Paths
# -----------------------------
BASE_DIR = Path(__file__).resolve().parents[2]  # project root
DATA_DIR = BASE_DIR / "artifacts" / "data"     # place csv files here
CONFIG_DIR = BASE_DIR / "config"

# Input filenames we expect (exactly your uploads)
CHEM_FILES = [
    "Alkalinity Total (CaCO3).csv",
    "Aluminium.csv",
    "Ammonia (Total).csv",
    "Arsenic.csv",
    "Cadmium.csv",
    "Chloride as Cl.csv",
    "Chromium.csv",
    "Colour (True Filtered).csv",
    "Copper.csv",
    "Dissolved Oxygen (DO).csv",
    "Fluoride as F.csv",
    "Iron.csv",
    "Lead.csv",
    "Manganese.csv",
    "Mercury.csv",
    "Molybdenum.csv",
    "Nickel.csv",
    "Nitrate (NO2).csv",
    "Nitrite (NO3).csv",
    "Selenium.csv",
    "Sodium as Na.csv",
    "Sulphate as SO4.csv",
    "Total Dissolved Solids (TDS).csv",
    "Turbidity.csv",
    "Zinc.csv",
]

GW_META = "GW Metadata.csv"  # col1 Site ID, col9 Suburb
SW_META = "SW Metadata.csv"  # col1 Site ID, col8 Water body, col9 Location

# Prophet parallelism (keep low for web-app)
os.environ.setdefault("PROPHET_MAX_WORKERS", "1")

# -----------------------------
# In-process caches
# -----------------------------
_lock = threading.RLock()
_cache_thresholds: Optional[Dict[str, float]] = None
_cache_gw_meta: Optional[pd.DataFrame] = None
_cache_sw_meta: Optional[pd.DataFrame] = None
_cache_chem_df: Dict[str, pd.DataFrame] = {}   # chem_name -> df(site_id, ds, y)
_cache_models: Dict[Tuple[str, str], Prophet] = {}  # key=(chem, key_id) where key_id=suburb or site_id

# -----------------------------
# Helpers
# -----------------------------
# threshold for each chemical
def _load_thresholds() -> Dict[str, Dict[str, object]]:
    """
    Returns a dict:
      {
        "Chemical Name": {"threshold": float, "direction": "below"|"above"},
        ...
      }
    """
    global _cache_thresholds
    with _lock:
        if _cache_thresholds is None:
            path = CONFIG_DIR / "thresholds.yaml"
            # handle absent file gracefully
            if not path.exists():
                _cache_thresholds = {}
                return _cache_thresholds

            with open(path, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}

            norm: Dict[str, Dict[str, object]] = {}
            for k, v in raw.items():
                name = str(k)
                if isinstance(v, (int, float, str)):
                    try:
                        thr = float(v)
                    except Exception:
                        raise ValueError(f"Threshold for '{name}' must be numeric, got: {v!r}")
                    norm[name] = {"threshold": thr, "direction": "below"}
                elif isinstance(v, dict):
                    if "threshold" not in v:
                        raise ValueError(f"Threshold mapping for '{name}' must include 'threshold'. Got: {v!r}")
                    thr = float(v["threshold"])
                    direction = str(v.get("direction", "below")).lower()
                    if direction not in ("below", "above"):
                        raise ValueError(f"direction for '{name}' must be 'below' or 'above', got: {direction!r}")
                    norm[name] = {"threshold": thr, "direction": direction}
                else:
                    raise ValueError(f"Unsupported threshold type for '{name}': {type(v).__name__}")
            _cache_thresholds = norm
        return _cache_thresholds
    

def _safe_float(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if isinstance(v, float) and (np.isnan(v) or np.isinf(v)):
        return None
    return v

# function for selecting suburbs for groundwater
def _read_meta_gw() -> pd.DataFrame:
    """Return GW metadata with columns: site_id, suburb"""
    global _cache_gw_meta
    with _lock:
        if _cache_gw_meta is not None:
            return _cache_gw_meta
        df = pd.read_csv(DATA_DIR / GW_META, low_memory=False)
        # Column indexes: col1 Site ID (0-based idx=0), col9 Suburb (idx=8)
        df = df.iloc[:, [0, 8]].copy()
        df.columns = ["site_id", "suburb"]
        df["site_id"] = df["site_id"].astype(str)
        df["suburb"] = df["suburb"].astype(str)
        _cache_gw_meta = df
        return df

# function for selecting water body and location for surface water
def _read_meta_sw() -> pd.DataFrame:
    """Return SW metadata with columns: site_id, water_body, location"""
    global _cache_sw_meta
    with _lock:
        if _cache_sw_meta is not None:
            return _cache_sw_meta
        df = pd.read_csv(DATA_DIR / SW_META, low_memory=False)
        # Column indexes: col1 Site ID, col8 Water body, col9 Location
        df = df.iloc[:, [0, 7, 8]].copy()
        df.columns = ["site_id", "water_body", "location"]
        df["site_id"] = df["site_id"].astype(str)
        df["water_body"] = df["water_body"].astype(str)
        df["location"] = df["location"].astype(str)
        _cache_sw_meta = df
        return df


def _read_chem(chem_file: str) -> pd.DataFrame:
    """
    Read a chemical CSV with columns:
      col1 -> site_id, col3 -> datetime, col8 -> value
    Return columns: site_id (str), ds (datetime64[ns]), y (float)
    """
    base = chem_file.replace(".csv", "")
    with _lock:
        if base in _cache_chem_df:
            return _cache_chem_df[base]
    path = DATA_DIR / chem_file
    # We only read necessary columns by index to keep memory low.
    df = pd.read_csv(path, usecols=[0, 2, 7], low_memory=False)
    df.columns = ["site_id", "ds", "y"]
    df["site_id"] = df["site_id"].astype(str)
    # robust datetime parse
    df["ds"] = pd.to_datetime(df["ds"], errors="coerce", utc=True)
    df["y"] = pd.to_numeric(df["y"], errors="coerce")
    df = df.dropna(subset=["site_id", "ds", "y"])
    # ensure sorted
    df = df.sort_values(["site_id", "ds"])
    with _lock:
        _cache_chem_df[base] = df
    return df


def list_gw_suburbs() -> List[str]:
    gw = _read_meta_gw()
    suburbs = sorted(gw["suburb"].dropna().unique().tolist(), key=str.lower)
    return suburbs


def list_sw_water_bodies() -> List[str]:
    sw = _read_meta_sw()
    items = sorted(sw["water_body"].dropna().unique().tolist(), key=str.lower)
    return items


def list_sw_locations(water_body: str) -> List[str]:
    sw = _read_meta_sw()
    locs = sw.loc[sw["water_body"] == str(water_body), "location"].dropna().unique().tolist()
    locs = sorted(locs, key=str.lower)
    return locs


def _resolve_site_id_from_sw(water_body: str, location: str) -> Optional[str]:
    sw = _read_meta_sw()
    hits = sw.loc[(sw["water_body"] == str(water_body)) & (sw["location"] == str(location)), "site_id"].unique()
    if len(hits) == 0:
        return None
    # If multiple site_ids share the same location, pick the first deterministically
    return str(sorted(hits, key=lambda x: str(x))[0])


# -----------------------------
# Eligibility counting (≥ 50)
# -----------------------------
def _count_records_gw_suburb(chem: str, suburb: str) -> int:
    """Count unique datetimes across all sites in suburb for this chemical."""
    df = _read_chem(f"{chem}.csv")
    gw = _read_meta_gw()
    sites = gw.loc[gw["suburb"] == suburb, "site_id"].unique().tolist()
    if not sites:
        return 0
    sub = df[df["site_id"].isin(sites)]
    if sub.empty:
        return 0
    # distinct timestamps (across sites)
    return int(sub["ds"].dt.normalize().nunique())  # daily distincts


def _count_records_sw_site(chem: str, site_id: str) -> int:
    df = _read_chem(f"{chem}.csv")
    sub = df[df["site_id"] == str(site_id)]
    if sub.empty:
        return 0
    return int(sub["ds"].dt.normalize().nunique())

def _to_naive_utc_dates(s: pd.Series) -> pd.Series:
    # Accept tz-aware or tz-naive, return tz-naive normalized dates
    s = pd.to_datetime(s, errors="coerce")
    if pd.api.types.is_datetime64tz_dtype(s):
        s = s.dt.tz_convert("UTC").dt.tz_localize(None)
    # normalize to midnight to match your daily aggregation
    return s.dt.normalize()

# -----------------------------
# Prophet training / predicting
# -----------------------------
def _ensure_model(chem: str, key_id: str, df_xy: pd.DataFrame) -> Prophet:
    """
    key_id is suburb (GW) or site_id (SW).
    df_xy must have columns ds (datetime64) and y (float).
    """
    cache_key = (chem, key_id)
    with _lock:
        if cache_key in _cache_models:
            return _cache_models[cache_key]

    # Prophet expects regular "ds","y". It can tolerate missing days; we just
    # aggregate to daily median to remove same-day duplicates.
    norm_ds = _to_naive_utc_dates(df_xy["ds"]).rename("ds")
    daily = (
        df_xy.assign(ds=norm_ds)
             .groupby("ds", as_index=False)["y"]
             .median()
    )

    daily = daily.dropna()
    # need enough history to be meaningful
    if len(daily) < 20:
        m = Prophet(seasonality_mode="additive", yearly_seasonality=True,
                    weekly_seasonality=False, daily_seasonality=False)
        m.fit(pd.DataFrame({
            "ds": pd.to_datetime(["2000-01-01"]),
            "y": [daily["y"].median() if len(daily) else 0.0],
        }))
        with _lock:
            _cache_models[cache_key] = m
        return m

    m = Prophet(
        seasonality_mode="additive",
        yearly_seasonality=True,
        weekly_seasonality=False,
        daily_seasonality=False,
        changepoint_prior_scale=0.1,
    )
    m.fit(daily)
    with _lock:
        _cache_models[cache_key] = m
    return m


def _predict_value(chem: str, model_key: str, df_xy: pd.DataFrame, date_str: str) -> Optional[float]:
    """
    Predict chem value for target date (UTC midnight). Returns float or None.
    """
    try:
        # Ensure model exists for (chem, model_key) using the provided series
        m = _ensure_model(chem, model_key, df_xy)

        # Build target timestamp as UTC date, but PROPHET REQUIRES TZ-NAIVE
        ts = pd.Timestamp(date_str)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        else:
            ts = ts.tz_convert("UTC")
        target = ts.normalize().tz_localize(None)  # tz-naive midnight UTC calendar date

        # Prophet expects a DataFrame with 'ds' (tz-naive)
        future = pd.DataFrame({"ds": [target]})

        fc = m.predict(future)
        if fc.empty or "yhat" not in fc.columns:
            return None

        return float(fc.iloc[0]["yhat"])
    except Exception:
        return None

# -----------------------------
# Public API – Groundwater (by suburb)
# -----------------------------
def predict_groundwater(suburb: str, date_str: str) -> Dict:
    """
    For a suburb:
      - a chemical is eligible if total records across the suburb ≥ 50 (distinct days).
      - for each eligible chemical, forecast on date_str
      - if predicted value < threshold → +1
      - percentage = passed / eligible * 100
    Returns dict with details for UI.
    """
    thresholds = _load_thresholds()
    gw = _read_meta_gw()

    # all site_ids in suburb
    sites = gw.loc[gw["suburb"] == suburb, "site_id"].unique().tolist()
    if not sites:
        return {"suburb": suburb, "eligible": 0, "passed": 0, "percentage": 0.0, "chemicals": []}

    included: List[Dict] = []
    eligible = 0
    passed = 0

    for chem_file in CHEM_FILES:
        chem = chem_file.replace(".csv", "")
        # eligibility: ≥ 50 records for suburb
        if _count_records_gw_suburb(chem, suburb) < 20:
            continue
        eligible += 1

        df = _read_chem(chem_file)
        # city-level series: stack all sites in suburb and aggregate to daily median
        sub_df = df[df["site_id"].isin(sites)][["ds", "y"]].copy()
        if sub_df.empty:
            continue

        yhat = _predict_value(chem, f"SUBURB::{suburb}", sub_df, date_str)
        tobj = thresholds.get(chem)  # dict with threshold + direction
        thr = None
        direction = "below"
        if isinstance(tobj, dict):
            thr = tobj.get("threshold", None)
            direction = tobj.get("direction", "below")

        pred_val = _safe_float(yhat)
        thr_val  = _safe_float(thr)

        ok = False
        if pred_val is not None and thr_val is not None:
            ok = (pred_val < thr_val) if direction == "below" else (pred_val > thr_val)
        if ok:
            passed += 1

        included.append({
            "chemical": chem,
            "predicted": pred_val,
            "threshold": thr_val,
            "direction": direction,
            "pass": bool(ok),
        })

    pct = float(passed / eligible * 100.0) if eligible > 0 else 0.0
    return {
        "suburb": suburb,
        "date": date_str,
        "eligible": eligible,
        "passed": passed,
        "percentage": round(pct, 2),
        "chemicals": included,
    }


# -----------------------------
# Public API – Surface water (by Site ID via water body → location)
# -----------------------------
def predict_surface(water_body: str, location: str, date_str: str) -> Dict:
    """
    For a chosen water body + location:
      - resolve Site ID, enforce ≥ 50 records per chemical at that site
      - if predicted value < threshold → +1
      - percentage = passed / eligible * 100
    """
    thresholds = _load_thresholds()
    site_id = _resolve_site_id_from_sw(water_body, location)

    if not site_id:
        return {"water_body": water_body, "location": location, "site_id": None,
                "eligible": 0, "passed": 0, "percentage": 0.0, "chemicals": []}

    included: List[Dict] = []
    eligible = 0
    passed = 0

    for chem_file in CHEM_FILES:
        chem = chem_file.replace(".csv", "")
        if _count_records_sw_site(chem, site_id) < 50:
            continue
        eligible += 1

        df = _read_chem(chem_file)
        site_df = df[df["site_id"] == site_id][["ds", "y"]].copy()
        if site_df.empty:
            continue

        yhat = _predict_value(chem, f"SITE::{site_id}", site_df, date_str)

        tobj = thresholds.get(chem)  # dict with threshold + direction
        thr = None
        direction = "below"
        if isinstance(tobj, dict):
            thr = tobj.get("threshold", None)
            direction = tobj.get("direction", "below")

        # ---- NEW: safe conversions (avoid float(None)/NaN/Inf) ----
        pred_val = _safe_float(yhat)
        thr_val  = _safe_float(thr)

        ok = False
        if pred_val is not None and thr_val is not None:
            ok = (pred_val < thr_val) if direction == "below" else (pred_val > thr_val)
        if ok:
            passed += 1

        included.append({
            "chemical": chem,
            "predicted": pred_val,   # None or float
            "threshold": thr_val,    # None or float
            "direction": direction,
            "pass": bool(ok),
        })

    pct = float(passed / eligible * 100.0) if eligible > 0 else 0.0
    return {
        "water_body": water_body,
        "location": location,
        "site_id": site_id,
        "date": date_str,
        "eligible": eligible,
        "passed": passed,
        "percentage": round(pct, 2),
        "chemicals": included,
    }


