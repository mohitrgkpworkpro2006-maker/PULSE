#!/usr/bin/env python3
"""
===============================================================================
KERALA LAND-USE EVALUATION AGENT — FastAPI BACKEND (SUGGESTIVE-ONLY)
===============================================================================
Evaluates a piece of land (lat/lng + building type) for construction viability.
The agent NEVER issues a verdict — it advises: evidence, score signal, risks,
and design measures. The user decides.

PIPELINE:
    1. Match nearest pilot-record (kerala_landuse_pilot_dataset) as precedent
    2. Fetch live OSM context (osm_data_fetcher_ai_clean.py, TTL-cached)
    3. Deterministic SCORE INDEX: 9 weighted dimensions + critical risk flags
    4. LLM agent (Ollama cloud API) writes the explainable pros/cons narrative
       around the index (never overrides it)
    5. Rule-based narrative fallback if the LLM call fails

RUN:
    export OLLAMA_API_KEY=your_key
    uvicorn ai_backend:app --reload --port 8000
===============================================================================
"""

import hashlib
import json
import logging
import math
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from osm_data_fetcher_ai_clean import AIFriendlyOSMFetcher

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s")
logger = logging.getLogger("landuse_backend")

# ============================================================================
# CONFIG (env-driven)
# ============================================================================
BASE_DIR = Path(__file__).resolve().parent
DATASET_PATH = Path(os.environ.get("PILOT_DATASET", BASE_DIR / "kerala_landuse_pilot_dataset (1).json"))
OLLAMA_URL = os.environ.get("OLLAMA_URL", "https://ollama.com/api/chat")
OLLAMA_API_KEY = os.environ.get("OLLAMA_API_KEY", "")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "gemma4:31b")
OLLAMA_TIMEOUT = int(os.environ.get("OLLAMA_TIMEOUT", "120"))
DEFAULT_RADIUS_M = int(os.environ.get("DEFAULT_RADIUS_M", "2000"))
OSM_CACHE_TTL = int(os.environ.get("OSM_CACHE_TTL", "600"))
EVALUATE_CACHE_TTL = int(os.environ.get("EVALUATE_CACHE_TTL", "120"))

# ============================================================================
# DATA MODELS
# ============================================================================
class EvaluateRequest(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lng: float = Field(..., ge=-180, le=180)
    building_type: str = Field(..., min_length=2, max_length=64,
                               description="e.g. residential_apartment, heritage_hotel, commercial_it_park ...")
    floors: Optional[int] = Field(None, ge=1, le=200)
    area_sqm: Optional[float] = Field(None, ge=1)
    radius_m: int = Field(DEFAULT_RADIUS_M, ge=100, le=10000)
    use_llm: bool = True


# ============================================================================
# 1. PILOT DATASET LOADER + NEAREST-RECORD MATCHER
# ============================================================================
def load_dataset(path: Path) -> Dict:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    records = {r["record_id"]: r for r in data.get("records", [])}
    logger.info("Loaded %d pilot records from %s", len(records), path.name)
    return {"metadata": data.get("metadata", {}), "records": records}


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


class DatasetIndex:
    def __init__(self, data: Dict):
        self.metadata = data.get("metadata", {})
        self.records = data.get("records", {})

    def nearest(self, lat: float, lng: float) -> Optional[Dict]:
        best, best_d = None, float("inf")
        for rec in self.records.values():
            d = haversine_m(lat, lng, rec.get("lat", 0), rec.get("lng", 0))
            if d < best_d:
                best, best_d = rec, d
        if best is None:
            return None
        return {**best, "_distance_m": round(best_d, 1)}


DATASET = DatasetIndex(load_dataset(DATASET_PATH))

# ============================================================================
# 2. OSM SERVICE (TTL cache)
# ============================================================================
_osm_cache: Dict[str, Dict] = {}
_osm_inflight: Dict[str, threading.Event] = {}
_osm_lock = threading.Lock()
_ev_cache: Dict[str, tuple] = {}
_osm_fetcher = AIFriendlyOSMFetcher()


def fetch_osm_context(lat: float, lng: float, radius_m: int) -> Dict:
    key = f"{round(lat, 5)},{round(lng, 5)},{radius_m}"
    entry = _osm_cache.get(key)
    if entry and time.time() - entry["ts"] < OSM_CACHE_TTL:
        return entry["data"]

    while True:
        with _osm_lock:
            if key in _osm_inflight:
                waiter = _osm_inflight[key]
            else:
                waiter = threading.Event()
                _osm_inflight[key] = waiter
                break
        logger.info("OSM fetch already in progress for %s — waiting", key)
        waiter.wait(timeout=OSM_CACHE_TTL)
        entry = _osm_cache.get(key)
        if entry and time.time() - entry["ts"] < OSM_CACHE_TTL:
            return entry["data"]
        time.sleep(0.2)

    try:
        logger.info("Fetching OSM context for %s (radius %dm)", key, radius_m)
        data = _osm_fetcher.fetch(lat, lng, radius_m)
        _osm_cache[key] = {"ts": time.time(), "data": data}
        return data
    finally:
        with _osm_lock:
            _osm_inflight.pop(key, None)
        waiter.set()


def _compact_osm(osm: Dict) -> Dict:
    """Keep only the compact AI-readable parts of the OSM report."""
    return {
        "osm_quality": osm.get("overview", {}).get("data_quality", {}),
        "parameters": {
            name: {
                "value": p.get("value"),
                "signal": p.get("signal"),
            }
            for name, p in osm.get("parameters", {}).items()
            if isinstance(p, dict)
        },
        "reasoning_signals": osm.get("reasoning_signals", {}),
    }


def _osm_param(osm: Dict, param: str, key: str = "value"):
    """Fetch `key` (value|signal) of an OSM parameter safely."""
    p = osm.get("parameters", {}).get(param, {})
    return p.get(key, {}) if isinstance(p, dict) else {}


# ============================================================================
# 3. SCORE INDEX (deterministic, dimension-weighted, suggestive)
# ============================================================================
def _tier(score: int) -> str:
    if score >= 80:
        return "good"
    if score >= 60:
        return "fair"
    if score >= 40:
        return "poor"
    return "critical"


def build_score_index(record: Dict, osm: Dict, req: EvaluateRequest) -> Dict:
    """9 weighted dimensions -> overall 0-100 index + descriptive level + flags."""

    def demand():
        growth = record.get("pop_growth_decadal_pct", 0) or 0
        density = record.get("pop_density_ppsqkm", 0) or 0
        if growth >= 15:
            s = 90
        elif growth >= 8:
            s = 75
        elif growth >= 2:
            s = 60
        elif growth >= 0:
            s = 45
        else:
            s = 25
        ev = [f"Population growth {growth:+.1f}%/decade ({record.get('pop_growth_period', '')})"]
        if density >= 4000:
            s += 10
        elif density >= 2000:
            s += 5
        elif density <= 1000:
            s -= 5
        ev.append(f"Density {density:,}/km²")
        return s, " ".join(ev)

    def economy():
        econ = record.get("econ_composite_score", 0) or 0
        s = econ * 10
        ev = [f"Economic composite {econ}/10"]
        density = (_osm_param(osm, "economic_activity").get("commercial_pois_per_sqkm") or 0)
        if density >= 50:
            s += 10
        elif density >= 15:
            s += 5
        ev.append(f"OSM commercial POIs: {density}/km²")
        return s, " ".join(ev)

    def flood():
        fr = record.get("flood_risk_score", 0) or 0
        s = (10 - fr) * 10
        ev = [f"Flood risk {fr}/10 ({record.get('flood_risk_category', 'n/a')})",
              f"{record.get('flood_risk_historical_events_20yr', 0)} events in 20 yrs"]
        elev = record.get("flood_risk_elevation_msl", 999) or 999
        if elev < 2:
            s -= 10
            ev.append(f"Elevation {elev}m MSL — low-lying")
        elif elev < 5:
            s -= 5
            ev.append(f"Elevation {elev}m MSL")
        proxy = _osm_param(osm, "flood_risk_water_proximity").get("flood_proxy_score") or 0
        if proxy >= 6:
            s -= 10
        elif proxy >= 3:
            s -= 5
        ev.append(f"OSM water-proximity proxy: {proxy}/10")
        return s, " · ".join(ev)

    def regulatory():
        if not record.get("crz_violation"):
            return 100, f"No CRZ violation ({record.get('crz_zone', 'Non-CRZ')})"
        sev = record.get("crz_violation_severity", "low")
        scores = {"critical": 5, "high": 15, "medium": 40, "low": 70}
        s = scores.get(sev, 60)
        ev = [f"CRZ violation ({record.get('crz_zone')}, severity {sev})"]
        if record.get("crz_ndz_flag"):
            s -= 20
            ev.append("No-Development-Zone flag set")
        if record.get("crz_setback_required_m"):
            ev.append(f"{record.get('crz_setback_required_m')}m setback required")
        return s, " · ".join(ev)

    def infrastructure():
        s = 100
        ev = []
        deficit = record.get("infra_water_deficit_pct", 0) or 0
        if deficit > 0:
            s -= min(deficit * 2.5, 50)
            ev.append(f"{deficit}% water supply deficit")
        load = record.get("infra_electricity_load_pct", 0) or 0
        if load >= 80:
            s -= 20
        elif load >= 65:
            s -= 10
        ev.append(f"Electricity grid at {load}% load")
        if record.get("infra_sewage_status") == "absent":
            s -= 15
            ev.append("Sewage: absent")
        cong = record.get("infra_road_congestion_index", 0) or 0
        if cong >= 8:
            s -= 10
        elif cong >= 6:
            s -= 5
        ev.append(f"Road congestion {cong}/10")
        if record.get("infra_overall_status") == "stressed":
            s -= 10
        ev.insert(0, f"Overall status: {record.get('infra_overall_status', 'n/a')}")
        return s, " · ".join(ev)

    def market():
        occ = record.get("comp_avg_occupancy_pct", 0) or 0
        if occ >= 70:
            s = 90
        elif occ >= 55:
            s = 75
        elif occ >= 40:
            s = 55
        else:
            s = 30
        ev = [f"Comparables {occ}% avg occupancy"]
        overhang = record.get("comp_inventory_overhang_months", 0) or 0
        if overhang <= 6:
            s += 10
        elif overhang > 24:
            s -= 20
        ev.append(f"{overhang} months inventory overhang")
        trend = record.get("comp_price_trend_yoy_pct", 0) or 0
        if trend >= 0:
            s += 5
        else:
            s -= 10
        ev.append(f"Price trend {trend:+.1f}%/yr")
        return s, " · ".join(ev)

    def tourism():
        rel = record.get("tourism_relevance", "low")
        mapping = {"very_high": 90, "high": 90, "seasonal_high": 75, "moderate": 60, "low": 35}
        s = mapping.get(rel, 35)
        ev = [f"Tourism relevance: {rel}"]
        pois = int((_osm_param(osm, "tourism_potential").get("total_tourism_pois") or 0))
        if pois > 5:
            s += 10
        ev.append(f"OSM tourism POIs: {pois}")
        if record.get("tourism_applicable") is False:
            ev.append("Not tourism-applicable (neutral)")
        return s, " · ".join(ev)

    def environmental():
        risk = record.get("env_impact_risk", "unknown")
        s = {"low": 90, "medium": 60, "high": 30}.get(risk, 60)
        ev = [f"Environmental impact risk: {risk}"]
        wetlands = (_osm_param(osm, "flood_risk_water_proximity").get("wetland_count") or 0)
        if wetlands > 0:
            s -= 10
            ev.append(f"OSM wetlands: {wetlands}")
        forest = (_osm_param(osm, "land_use_environment").get("forest_zones") or 0)
        if forest > 0:
            s -= 10
            ev.append(f"OSM forest zones: {forest}")
        return s, " · ".join(ev)

    def historical():
        s = 90
        ev = []
        if record.get("hist_recurring_risk"):
            s -= 40
            ev.append("Recurring historical risk")
        else:
            ev.append("No recurring historical risk")
        incidents = record.get("hist_incidents_5km", 0) or 0
        if incidents >= 3:
            s -= 20
        elif incidents >= 1:
            s -= 10
        ev.append(f"{incidents} incidents within 5km")
        depth = record.get("hist_last_major_flood_depth_m", 0) or 0
        if depth >= 2:
            s -= 10
            ev.append(f"Last major flood depth {depth}m")
        driver = record.get("hist_primary_risk_driver")
        if driver and driver != "none_significant":
            ev.append(f"Primary driver: {driver}")
        return s, " · ".join(ev)

    dims = [
        ("demand", "Population & Demand", 15, demand),
        ("economy", "Economy & Commercial Activity", 10, economy),
        ("flood", "Flood & Hydrology", 20, flood),
        ("regulatory", "Regulatory / CRZ Compliance", 20, regulatory),
        ("infrastructure", "Infrastructure Capacity", 10, infrastructure),
        ("market", "Market Viability", 10, market),
        ("tourism", "Tourism & Hospitality", 5, tourism),
        ("environment", "Environmental Impact", 5, environmental),
        ("historical", "Historical Risk", 5, historical),
    ]

    dimensions = []
    overall = 0.0
    for dim_id, label, weight, fn in dims:
        score, evidence = fn()
        score = max(0, min(100, int(round(score))))
        overall += score * weight / 100.0
        dimensions.append({
            "id": dim_id, "label": label, "weight": weight,
            "score": score, "status": _tier(score), "evidence": evidence,
        })
    overall = max(0, min(100, int(round(overall))))

    # ---- Critical risk flags (attention items — never decide for the user) ----
    flags = []
    sev = record.get("crz_violation_severity", "none") if record.get("crz_violation") else "none"
    if sev == "critical":
        flags.append(f"CRITICAL CRZ violation ({record.get('crz_zone')}) — precedent: demolition/legal action")
    elif sev == "high" and record.get("crz_ndz_flag"):
        flags.append(f"High-severity CRZ violation in No-Development Zone ({record.get('crz_zone')})")
    if record.get("crz_ndz_flag"):
        flags.append("Site flagged in a No-Development Zone — permanent construction likely prohibited")
    fr = record.get("flood_risk_score", 0) or 0
    if fr >= 8 and (record.get("flood_risk_historical_events_20yr") or 0) > 0:
        flags.append(f"Extreme flood risk {fr}/10 with {record.get('flood_risk_historical_events_20yr')} historical events")
    growth = record.get("pop_growth_decadal_pct", 0) or 0
    econ = record.get("econ_composite_score", 0) or 0
    if growth < 0 and econ < 4:
        flags.append(f"Shrinking population ({growth:+.1f}%) with weak economy ({econ}/10) — demand structurally absent")
    if (record.get("infra_water_deficit_pct") or 0) > 20:
        flags.append(f"Water supply deficit of {record.get('infra_water_deficit_pct')}%")
    if (record.get("comp_inventory_overhang_months") or 0) > 24:
        flags.append(f"{record.get('comp_inventory_overhang_months')} months inventory overhang — saturated market")

    # ---- Descriptive level of the score (suggestive, not a verdict) ----
    level = "high" if overall >= 70 else "medium" if overall >= 45 else "low"

    completeness = record.get("data_completeness_pct", 95) or 95
    confidence = "high" if completeness >= 95 else "medium" if completeness >= 85 else "low"

    return {
        "overall": overall,
        "level": level,
        "confidence": confidence,
        "critical_flags": flags,
        "dimensions": dimensions,
    }


# ============================================================================
# 4. CONSTRUCTION CONSIDERATIONS (risk -> mitigation checklist)
# ============================================================================
def build_construction_considerations(record: Dict, osm: Dict, req: EvaluateRequest) -> List[Dict]:
    items = []
    def add(risk, measure, priority):
        items.append({"risk": risk, "measure": measure, "priority": priority})

    fr = record.get("flood_risk_score", 0) or 0
    elev = record.get("flood_risk_elevation_msl", 999) or 999
    depth = record.get("hist_last_major_flood_depth_m", 0) or 0
    if fr >= 8:
        add(f"Extreme flood risk ({fr}/10)", f"Elevate plinth ≥ {max(depth, 1.5)}m above last flood depth; no basements; flood barriers; elevated MEP", "critical")
    elif fr >= 6.5:
        add(f"High flood risk ({fr}/10)", "Elevate plinth ≥ +2m; flood-proof substructure; rain-water drainage design", "high")
    elif fr >= 4.5:
        add(f"Moderate flood risk ({fr}/10)", "Storm-water drainage, ground-level cutoffs, water-proofing of parking levels", "medium")
    if elev < 2:
        add(f"Low elevation ({elev}m MSL)", "Pile foundations to bearing strata; consider hydraulic fill / raised platform", "high")

    if record.get("crz_violation"):
        sev = record.get("crz_violation_severity", "low")
        setback = record.get("crz_setback_required_m")
        if sev == "critical":
            add(f"CRITICAL CRZ violation in {record.get('crz_zone')}", f"Construction effectively barred — obtain KCZMA clearance first; respect {setback}m HTL setback", "critical")
        else:
            add(f"CRZ restrictions ({record.get('crz_zone')})", f"KCZMA clearance mandatory; comply with {setback}m HTL setback; no permanent structures in NDZ", "high")
    if record.get("crz_ndz_flag"):
        add("CRZ No-Development Zone", "No permanent construction permitted — explore relocation or litigation risk", "critical")

    deficit = record.get("infra_water_deficit_pct", 0) or 0
    if deficit > 10:
        add(f"Water supply deficit ({deficit}%)", "Rainwater harvesting, grey-water recycling, storage tanks, water audit", "high")
    if record.get("infra_sewage_status") == "absent":
        add("No sewage connectivity", "Mandatory on-site sewage treatment plant (STP) + recharge pit design", "high")
    load = record.get("infra_electricity_load_pct", 0) or 0
    if load >= 80:
        add(f"Grid at {load}% load", "Backup generator + rooftop solar; electrical design with failsafe", "medium")
    cong = record.get("infra_road_congestion_index", 0) or 0
    if cong >= 7:
        add(f"Road congestion {cong}/10", "Traffic study, dedicated parking provision, access-road planning", "medium")
    if (record.get("infra_road_width_m") or 0) < 10:
        add(f"Access road {record.get('infra_road_width_m')}m wide", "Verify fire-tender approach and right-of-way norms", "low")

    wetlands = (_osm_param(osm, "flood_risk_water_proximity").get("wetland_count") or 0)
    if wetlands > 0 or record.get("env_impact_risk") == "high":
        add("Wetland / soft-soil proximity", "Geotechnical soil investigation, deep piling; zero wetland fill; environmental clearance", "high")
    if record.get("hist_primary_risk_driver") == "landslide":
        add("Landslide-prone terrain", "Geological survey, slope stabilization, cut-and-fill balance, monsoon drainage", "high")
    if "cyclone" in str(record.get("hist_primary_risk_driver", "")) or "cyclone" in str(record.get("hist_incident_2_type", "")):
        add("Cyclone exposure", "Wind-resistant design per IS 875 / IS 1893; roof anchorage; impact glazing", "high")

    if record.get("crz_authority_order") and record["crz_authority_order"] not in ("NA", ""):
        add("Regulatory authority order on record", f"{record['crz_authority_order']}", "high")
    if record.get("tourism_relevance") == "seasonal_high":
        add("Seasonal tourism dependency", "Design for peak-season load; plan off-season revenue streams", "low")

    shadow = record.get("env_shadow_affected_parcels", 0) or 0
    if shadow >= 8:
        add(f"Shadow impacts {shadow} nearby parcels", "Shadow study + massing mitigation; setback/height trimming", "medium")
    max_floors_osm = (_osm_param(osm, "building_density").get("max_floors_nearby")) or 0
    if req.floors and max_floors_osm and req.floors >= 8 and max_floors_osm < 8:
        add(f"Height exceeds local context (max {max_floors_osm} floors nearby)", "Structural review, wind study, fire engineering — tall building stands out", "medium")

    twin = record.get("comp_ghost_twin_1_lessons")
    if twin and twin != "NA":
        add(f"Precedent lesson ({record.get('comp_ghost_twin_1_name', 'ghost twin')})", twin, "medium")

    return items


# ============================================================================
# 5. CLIENT CONTEXT BLOCK (curated, display-ready)
# ============================================================================
def _kv(label, value, unit=""):
    """Key-value-unit display object."""
    return {"label": label, "value": value, "unit": unit}


def build_client_context(record: Dict, osm: Dict, osm_error: Optional[str]) -> Dict:
    # ---- Site & provenance ----
    site = {
        "record_id": record["record_id"],
        "location_name": record.get("location_name"),
        "city": record.get("city"),
        "district": record.get("district"),
        "pincode": record.get("pincode"),
        "admin_type": record.get("admin_type"),
        "buffer_radius_m": record.get("buffer_radius_m"),
        "distance_m": record.get("_distance_m"),
        "pilot_verdict": {
            "value": record.get("agent_verdict"),
            "source": "pilot dataset (reference only — agent issues no verdict)",
        },
        "note": "Nearest validated precedent from the pilot dataset — not the queried site itself.",
    }

    # ---- Trust layer ----
    dims_with_sources = {
        "pop_density": "pop_density_source", "pop_growth": "pop_growth_source",
        "flood_risk": "flood_risk_source", "crz": "crz_source",
        "infrastructure": "infra_source", "comparables": "comp_source",
        "economy": "econ_source", "tourism": "tourism_source",
        "environment": "env_source", "historical": "hist_source",
    }
    dims_with_confidence = {
        "pop_density": "pop_density_confidence", "pop_growth": "pop_growth_confidence",
        "flood_risk": "flood_risk_confidence", "crz": "crz_confidence",
        "infrastructure": "infra_confidence", "comparables": "comp_confidence",
        "economy": "econ_confidence", "tourism": "tourism_confidence",
        "environment": "env_confidence", "historical": "hist_confidence",
    }
    trust = {
        "data_completeness_pct": record.get("data_completeness_pct"),
        "dimension_sources": {k: record.get(v) for k, v in dims_with_sources.items() if record.get(v)},
        "dimension_confidence": {k: record.get(v) for k, v in dims_with_confidence.items() if record.get(v)},
        "osm_coverage_estimate": osm.get("overview", {}).get("data_quality", {}).get("osm_coverage_estimate"),
        "dataset_note": DATASET.metadata.get("data_quality_note"),
    }

    # ---- Regulatory ----
    regulatory = {
        "crz_zone": record.get("crz_zone"),
        "violation": record.get("crz_violation"),
        "severity": record.get("crz_violation_severity"),
        "distance_from_htl_m": record.get("crz_distance_from_htl_m"),
        "setback_required_m": record.get("crz_setback_required_m"),
        "ndz_flag": record.get("crz_ndz_flag"),
        "authority_order": record.get("crz_authority_order") if record.get("crz_authority_order") not in ("NA", "") else None,
    }

    # ---- Precedent lessons (mistakes never repeated) ----
    lessons = []
    for twin_key in ("comp_ghost_twin_1", "comp_ghost_twin_2"):
        name = record.get(f"{twin_key}_name")
        if name and name != "NA":
            lessons.append({
                "type": "comparable_precedent",
                "name": name,
                "status": record.get(f"{twin_key}_status"),
                "distance_m": record.get(f"{twin_key}_distance_m"),
                "lesson": record.get(f"{twin_key}_lessons"),
            })
    for inc_key in ("hist_incident_1", "hist_incident_2"):
        name = record.get(f"{inc_key}_name")
        if name and name != "NA":
            lessons.append({
                "type": "historical_incident",
                "name": name,
                "year": record.get(f"{inc_key}_year"),
                "event_type": record.get(f"{inc_key}_type"),
                "loss_inr_cr": record.get(f"{inc_key}_loss_inr_cr"),
            })

    # ---- KPI snapshot cards ----
    kpi = [
        _kv("Population density", record.get("pop_density_ppsqkm"), "ppl/km²"),
        _kv("Population growth", f"{record.get('pop_growth_decadal_pct', 0):+.1f}".replace("+0.0", "0.0") if record.get("pop_growth_decadal_pct") is not None else None, "%/decade"),
        _kv("Flood risk", record.get("flood_risk_score"), "/10"),
        _kv("Flood category", record.get("flood_risk_category")),
        _kv("Elevation", record.get("flood_risk_elevation_msl"), "m MSL"),
        _kv("Flood events (20 yr)", record.get("flood_risk_historical_events_20yr")),
        _kv("Nearest river", record.get("env_nearest_river_name"), f"{record.get('env_nearest_river_dist_m', 0)}m" if record.get("env_nearest_river_name") else ""),
        _kv("Comparable price", record.get("comp_avg_price_psf_inr"), "₹/sqft"),
        _kv("Price trend (YoY)", f"{record.get('comp_price_trend_yoy_pct', 0):+.1f}".replace("+0.0", "0.0") if record.get("comp_price_trend_yoy_pct") is not None else None, "%"),
        _kv("Inventory overhang", record.get("comp_inventory_overhang_months"), "months"),
        _kv("Comparable occupancy", record.get("comp_avg_occupancy_pct"), "%"),
        _kv("Economic activity", record.get("econ_composite_score"), "/10"),
        _kv("Tourism footfall", record.get("tourism_district_annual_footfall"), "visitors/yr"),
        _kv("Tourism season", f"{record.get('tourism_peak_season')} / {record.get('tourism_off_peak')}" if record.get("tourism_peak_season") not in (None, "NA") else None),
        _kv("Water deficit", record.get("infra_water_deficit_pct"), "%"),
        _kv("Grid load", record.get("infra_electricity_load_pct"), "%"),
        _kv("Road congestion", record.get("infra_road_congestion_index"), "/10"),
        _kv("Sewage", record.get("infra_sewage_status")),
    ]
    kpi = [k for k in kpi if k["value"] is not None]

    # ---- Live OSM chips ----
    stats = osm.get("overview", {}).get("fetch_stats", {})
    bd = _osm_param(osm, "building_density")
    rd = _osm_param(osm, "road_connectivity")
    ea = _osm_param(osm, "economic_activity")
    tp = _osm_param(osm, "tourism_potential")
    lu = _osm_param(osm, "land_use_environment")
    pw = _osm_param(osm, "power_infrastructure")
    live_osm = {
        "fetched_at": stats.get("timestamp"),
        "coverage": osm.get("overview", {}).get("data_quality", {}).get("osm_coverage_estimate"),
        "stats": {
            "buildings": stats.get("buildings"),
            "roads": stats.get("roads"),
            "water_bodies": stats.get("water_bodies"),
            "amenities": stats.get("amenities"),
            "shops": stats.get("shops"),
            "tourism_pois": stats.get("tourism_pois"),
            "landuse_zones": stats.get("landuse_zones"),
        },
        "metrics": [
            _kv("Building density", bd.get("buildings_per_sqkm"), "/km²"),
            _kv("Avg floors nearby", bd.get("avg_floors_nearby")),
            _kv("Max floors nearby", bd.get("max_floors_nearby")),
            _kv("Nearest road", rd.get("nearest_road_name"), f"{rd.get('nearest_road_m')}m" if rd.get("nearest_road_m") else ""),
            _kv("Road type", rd.get("nearest_road_type")),
            _kv("Commercial POIs", ea.get("commercial_pois_per_sqkm"), "/km²"),
            _kv("Top amenities", ", ".join(f"{k} ({v})" for k, v in list((ea.get("top_amenity_types") or {}).items())[:4])),
            _kv("Hotels nearby", tp.get("hotels_nearby")),
            _kv("Attractions", tp.get("attractions_nearby")),
            _kv("Substations", pw.get("substations_nearby")),
        ],
        "land_use_breakdown": lu.get("landuse_breakdown") or {},
    }
    live_osm["metrics"] = [m for m in live_osm["metrics"] if m["value"] is not None]

    # ---- Cautions (things to verify) ----
    cautions = list(osm.get("reasoning_signals", {}).get("caution_flags", []))
    for dim, conf in trust["dimension_confidence"].items():
        if conf != "high":
            cautions.append(f"Data confidence {dim}: {conf} — treat related signals as indicative")
    if osm_error:
        cautions.insert(0, f"Live OSM unavailable ({osm_error}) — relying on pilot record only")
    elif trust["osm_coverage_estimate"] in ("sparse", "very_sparse"):
        cautions.insert(0, f"OSM coverage {trust['osm_coverage_estimate']} — live signals may be incomplete")
    return {
        "site": site,
        "trust": trust,
        "regulatory": regulatory,
        "precedent_lessons": lessons,
        "kpi": kpi,
        "live_osm": live_osm,
        "cautions": list(dict.fromkeys(cautions)),
    }


# ============================================================================
# 6. NARRATIVE (rule fallback: derived straight from the score index)
# ============================================================================
def rule_narrative(index: Dict, osm: Dict, considerations: List[Dict]) -> Dict:
    pros = [f"Strong {d['label'].lower()} ({d['score']}/100): {d['evidence']}"
            for d in index["dimensions"] if d["score"] >= 70][:4]
    cons = [f"Weak {d['label'].lower()} ({d['score']}/100): {d['evidence']}"
            for d in sorted(index["dimensions"], key=lambda d: d["score"]) if d["score"] < 60][:6]
    for s in osm.get("reasoning_signals", {}).get("positive_indicators", [])[:2]:
        pros.append(f"OSM live signal: {s}")
    for s in osm.get("reasoning_signals", {}).get("negative_indicators", [])[:2]:
        cons.append(f"OSM live signal: {s}")
    for s in osm.get("reasoning_signals", {}).get("caution_flags", [])[:2]:
        cons.append(f"OSM caution: {s}")
    for f in index["critical_flags"]:
        cons.insert(0, "ATTENTION: " + f)

    mitigations = [c["measure"] for c in considerations if c["priority"] in ("critical", "high")][:5]
    dominant = [d["label"] for d in index["dimensions"] if d["score"] >= 70]
    weak = [d["label"] for d in index["dimensions"] if d["score"] < 45]
    reasoning = (
        f"Suggestive score index: {index['overall']}/100 (level: {index['level']}) across 9 weighted dimensions."
        + (f" Critical flags worth attention: " + "; ".join(index["critical_flags"]) + "." if index["critical_flags"] else "")
        + (f" Strengths: " + ", ".join(dominant)[:200] + "." if dominant else "")
        + (f" Weaknesses: " + ", ".join(weak)[:200] + "." if weak else "")
        + " The agent advises only — final decision rests with the user."
    )
    return {
        "pros": pros,
        "cons": cons,
        "mitigations": mitigations,
        "reasoning": reasoning,
    }


# ============================================================================
# 6b. CLIENT VERDICT BLOCK (suggestive decision + blocking factors)
# ============================================================================
def build_client_verdict(record: Dict, index: Dict) -> Dict:
    """Map the deterministic index into a client-facing, user-decides verdict."""
    if index["overall"] >= 70 and not index["critical_flags"]:
        decision, label = "proceed", "Proceedable"
        fmt = "green"
    elif index["overall"] >= 45:
        decision, label = "proceed_with_caution", "Proceed only under conditions"
        fmt = "amber"
    else:
        decision, label = "do_not_proceed", "Not advisable in current form"
        fmt = "red"

    factor_meta = {
        "crz_violation": ("Regulatory / CRZ compliance", "crz_zone", "crz_violation_severity"),
        "flood_risk": ("Flood & hydrology", "flood_risk_category", "flood_risk_score"),
        "infrastructure_deficit": ("Infrastructure capacity", "infra_overall_status", "infra_water_deficit_pct"),
    }
    blocking = []
    for f in (record.get("agent_blocking_factors") or "").split(";"):
        f = f.strip()
        if not f:
            continue
        label_f, ev_key, sev_key = factor_meta.get(f, (f.replace("_", " ").title(), None, None))
        blocking.append({
            "factor": f,
            "label": label_f,
            "evidence": record.get(ev_key),
            "severity": record.get(sev_key),
        })

    if blocking and decision == "proceed":
        decision, label, fmt = "proceed_with_caution", "Proceed only under conditions", "amber"

    summary = (f"Score index {index['overall']}/100 (level {index['level']}) across 9 weighted dimensions."
               + (f" Blocking factors on record: {', '.join(b['label'].lower() for b in blocking)}."
                  if blocking else "")
               + " This is advisory — the final decision rests with you.")

    return {
        "decision": decision,
        "label": label,
        "color_signal": fmt,
        "level": index["level"],
        "summary": summary,
        "blocking_factors": blocking,
        "critical_flags": index["critical_flags"],
    }


# ============================================================================
# 7. LLM AGENT (Ollama cloud) — narrative around the authoritative index
# ============================================================================
SYSTEM_PROMPT = """You are an expert land-use evaluation advisor for Kerala, India. A municipality, investor, or citizen proposes constructing a specific building at given coordinates. You provide an integrated, transparent, explainable ASSESSMENT — you never issue a verdict. The user decides.

You are given:
- REQUESTED: incoming-proposal coordinates and building type/floors/area.
- PRECEDENT RECORD: the geographically nearest record from a government-backed pilot dataset. Treat it as the closest validated profile for the region — NOT the site itself.
- OSM CONTEXT: live OpenStreetMap analysis around the exact queried coordinates.
- SCORE INDEX: a deterministic 0-100 index of 9 weighted dimensions, plus critical risk flags you should respect in your reasoning.

Rules:
1. Advise only. No verdicts, no approval/rejection language — describe evidence, risks, and what conditions would make the site acceptable.
2. Every pro/con MUST cite the actual number or flag supplied (flood score, CRZ status, water deficit, occupancy %, population growth, dimension score, OSM signals). Do not invent data.
3. Critical flags must appear in your cons (or reasoning) — they are the strongest evidence.
4. mitigate: give concrete design/process measures (plinth elevation, clearances, STP, piling, parking, etc.).
5. Explainable reasoning: a short paragraph connecting the strongest evidence and dimension scores, including conditions under which the user could proceed.
6. Return ONLY a JSON object with this exact schema:
{"score": 0-100, "pros": ["..."], "cons": ["..."], "mitigations": ["..."], "reasoning": "..."}"""


def _build_user_prompt(req: EvaluateRequest, record: Dict, osm: Dict, index: Dict) -> str:
    compact = {
        "proposal": {
            "lat": req.lat, "lng": req.lng, "building_type": req.building_type,
            "floors": req.floors, "area_sqm": req.area_sqm, "radius_m": req.radius_m,
        },
        "precedent_record": record,
        "osm_context": _compact_osm(osm),
        "score_index": {
            "overall": index["overall"],
            "level": index["level"],
            "critical_flags": index["critical_flags"],
            "dimensions": [
                {k: d[k] for k in ("id", "label", "score", "status", "weight", "evidence")}
                for d in index["dimensions"]
            ],
        },
    }
    return json.dumps(compact, ensure_ascii=False, indent=2, default=str)


def _call_ollama(prompt: str, timeout: int = OLLAMA_TIMEOUT) -> Dict:
    if not OLLAMA_API_KEY:
        raise RuntimeError("OLLAMA_API_KEY not set")
    if OLLAMA_URL.rstrip("/").endswith("/api"):
        url = OLLAMA_URL.rstrip("/") + "/chat"
    else:
        url = OLLAMA_URL
    headers = {
        "Authorization": f"Bearer {OLLAMA_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.2},
    }
    resp = requests.post(OLLAMA_URL, headers=headers, json=payload, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _parse_llm_assessment(raw: str) -> Dict:
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    obj = json.loads(text)
    return {
        "score": max(0, min(100, int(float(obj.get("score", 50))))),
        "pros": [str(p) for p in obj.get("pros", [])][:6],
        "cons": [str(c) for c in obj.get("cons", [])][:8],
        "mitigations": [str(m) for m in obj.get("mitigations", [])][:6],
        "reasoning": str(obj.get("reasoning", "")),
    }


def llm_agent(req: EvaluateRequest, record: Dict, osm: Dict, index: Dict) -> Dict:
    prompt = _build_user_prompt(req, record, osm, index)
    start = time.time()
    raw = _call_ollama(prompt)
    content = raw.get("message", {}).get("content", "")
    result = _parse_llm_assessment(content)
    result["model"] = raw.get("model", OLLAMA_MODEL)
    result["latency_s"] = round(time.time() - start, 1)
    return result


# ============================================================================
# 8. FASTAPI APP
# ============================================================================
app = FastAPI(
    title="Kerala Land-Use Evaluation Agent",
    version="3.0.0",
    description="Suggestive-only evaluation of construction feasibility at any lat/lng in Kerala. The agent advises; the user decides.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "time": datetime.now(timezone.utc).isoformat(),
        "ollama_configured": bool(OLLAMA_API_KEY),
        "ollama_model": OLLAMA_MODEL,
        "pilot_records": len(DATASET.records),
    }


@app.get("/api/records")
def records():
    return {
        "metadata": DATASET.metadata,
        "records": [
            {
                "record_id": r["record_id"],
                "location_name": r["location_name"],
                "city": r.get("city"),
                "district": r.get("district"),
                "lat": r.get("lat"),
                "lng": r.get("lng"),
                "building_type": r.get("requested_building_type"),
                "agent_verdict": r.get("agent_verdict"),
            }
            for r in DATASET.records.values()
        ],
    }


@app.get("/api/records/{record_id}")
def record_detail(record_id: str):
    """Full raw pilot record (all fields incl. sources/confidence/lessons)."""
    rec = DATASET.records.get(record_id.upper())
    if rec is None:
        raise HTTPException(status_code=404, detail=f"Record {record_id} not found")
    return {
        "metadata": DATASET.metadata,
        "record": rec,
    }


@app.post("/api/evaluate")
def evaluate(req: EvaluateRequest):
    start = time.time()

    req_key = hashlib.sha256(
        json.dumps(req.model_dump(), sort_keys=True, default=str).encode()
    ).hexdigest()
    now = time.time()
    hit = _ev_cache.get(req_key)
    if hit and now - hit[0] < EVALUATE_CACHE_TTL:
        logger.info("Serving cached evaluate response (%s) — dedup hit", req_key[:8])
        return hit[1]

    record = DATASET.nearest(req.lat, req.lng)
    if record is None:
        raise HTTPException(status_code=500, detail="Pilot dataset is empty")

    try:
        osm = fetch_osm_context(req.lat, req.lng, req.radius_m)
        osm_error = None
    except Exception as exc:
        logger.warning("OSM fetch failed: %s", exc)
        osm, osm_error = {}, str(exc)

    # ---- Authoritative score index (always deterministic) ----
    index = build_score_index(record, osm, req)

    # ---- Construction guidance + curated client context (always deterministic) ----
    considerations = build_construction_considerations(record, osm, req)
    context = build_client_context(record, osm, osm_error)

    # ---- Narrative: LLM preferred, rule fallback otherwise ----
    llm_info, llm_error, narrative = None, None, None
    if req.use_llm:
        try:
            out = llm_agent(req, record, osm, index)
            llm_info = {"model": out.pop("model"), "latency_s": out.pop("latency_s")}
            narrative = out
            engine = "ollama-llm"
        except Exception as exc:
            logger.warning("LLM agent failed (%s); using rule narrative", exc)
            llm_error, narrative, engine = str(exc), None, "rule-fallback"
    else:
        engine = "rule-fallback"

    if narrative is None:
        narrative = rule_narrative(index, osm, considerations)
        if llm_error:
            narrative["reasoning"] = f"LLM unavailable ({llm_error}). " + narrative["reasoning"]

    # ---- Suggestive output: index informs, narrative explains, user decides ----
    response = {
        "request": req.model_dump(),
        "record_id": record["record_id"],
        "engine": engine,
        "llm": llm_info,
        "llm_error": llm_error,
        "verdict": build_client_verdict(record, index),
        "reference_record": {
            "record_id": record["record_id"],
            "location_name": record["location_name"],
            "district": record["district"],
            "distance_m": record["_distance_m"],
        },
        "score_index": index,
        "score": index["overall"],
        "confidence": index["confidence"],
        "pros": narrative["pros"],
        "cons": narrative["cons"],
        "mitigations": narrative["mitigations"],
        "reasoning": narrative["reasoning"],
        "construction_considerations": considerations,
        "context": context,
        "osm": {
            "error": osm_error,
            "quality": osm.get("overview", {}).get("data_quality", {}),
            "llm_context_block": osm.get("llm_context_block"),
        },
        "timing_ms": int((time.time() - start) * 1000),
    }
    _ev_cache[req_key] = (time.time(), response)
    logger.info("Cached evaluate response (%s) for %ds", req_key[:8], EVALUATE_CACHE_TTL)
    return response


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)