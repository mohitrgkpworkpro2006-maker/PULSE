#!/usr/bin/env python3
"""
================================================================================
KERALA LAND-USE AGENT — AI-FRIENDLY OSM DATA FETCHER (CLEAN)
================================================================================
Fetches OpenStreetMap data and structures it for direct LLM/AI agent consumption.
Output focuses ONLY on available OSM data — no missing data mentions.

INPUT:  lat, lng from frontend
OUTPUT: AI-optimized JSON with pre-computed reasoning signals and summaries

USAGE:
    python osm_data_fetcher_ai_clean.py --lat 9.9345 --lng 76.3123 --radius 1000

DEPENDENCIES:
    pip install requests
================================================================================
"""

import argparse
import json
import logging
import math
import time
from datetime import datetime
from typing import Dict, List

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s")
logger = logging.getLogger("osm_ai_fetcher")

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OVERPASS_BACKUP = "https://overpass.kumi.systems/api/interpreter"
REQUEST_TIMEOUT = 120
RATE_LIMIT = 2
MAX_ATTEMPTS = 3
RETRY_BACKOFF_BASE = 2
RETRY_BACKOFF_MAX = 5
TIMEOUT_REMARK_HINTS = ("timed out", "runtime error", "error")


def build_ai_output(raw_osm_data: Dict, lat: float, lng: float, radius_m: int) -> Dict:
    """Transform raw OSM elements into clean AI-friendly structured report."""

    start = time.time()
    elements = raw_osm_data.get("elements", [])
    area_sqkm = round(math.pi * (radius_m / 1000) ** 2, 3)

    # Categorize
    buildings = [e for e in elements if e.get("tags", {}).get("building")]
    roads = [e for e in elements if e.get("tags", {}).get("highway")]
    water = [e for e in elements if any(k in e.get("tags", {}) for k in ["waterway", "natural", "wetland", "water"])]
    amenities = [e for e in elements if e.get("tags", {}).get("amenity")]
    shops = [e for e in elements if e.get("tags", {}).get("shop")]
    tourism = [e for e in elements if e.get("tags", {}).get("tourism")]
    landuse = [e for e in elements if e.get("tags", {}).get("landuse")]
    power = [e for e in elements if e.get("tags", {}).get("power")]

    # Haversine
    def dist_m(lat1, lon1, lat2, lon2):
        R = 6371000
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlambda = math.radians(lon2 - lon1)
        a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
        return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

    def nearest(items, lat, lng):
        min_d, best = float('inf'), None
        for item in items:
            ilat, ilng = item.get("lat"), item.get("lon")
            if ilat and ilng:
                d = dist_m(lat, lng, ilat, ilng)
                if d < min_d:
                    min_d, best = d, item
        return (round(min_d, 1), best) if min_d != float('inf') else (None, None)

    # ==================== BUILDING DENSITY ====================
    bldg_types = {}
    bldg_categories = {"residential": 0, "commercial": 0, "industrial": 0, "public": 0, "hospitality": 0, "other": 0}
    levels_list = []

    for b in buildings:
        bt = b["tags"].get("building", "yes")
        bldg_types[bt] = bldg_types.get(bt, 0) + 1
        cat = "other"
        if bt in ["house","detached","terrace","semidetached_house","bungalow","villa","residential","apartments"]:
            cat = "residential"
        elif bt in ["commercial","retail","shop","supermarket","mall","store","warehouse","office"]:
            cat = "commercial"
        elif bt in ["industrial","factory","manufacture","works"]:
            cat = "industrial"
        elif bt in ["school","university","college","hospital","clinic","government","civic","public"]:
            cat = "public"
        elif bt in ["hotel","motel","hostel","guest_house"]:
            cat = "hospitality"
        bldg_categories[cat] += 1
        lv = b["tags"].get("building:levels")
        if lv:
            try: levels_list.append(float(lv))
            except: pass

    density = round(len(buildings) / area_sqkm, 1)
    avg_levels = round(sum(levels_list)/len(levels_list), 1) if levels_list else None
    max_levels = int(max(levels_list)) if levels_list else None

    param_building_density = {
        "parameter_name": "Building Density & Urbanization",
        "value": {
            "total_buildings": len(buildings),
            "buildings_per_sqkm": density,
            "residential": bldg_categories["residential"],
            "commercial": bldg_categories["commercial"],
            "industrial": bldg_categories["industrial"],
            "public": bldg_categories["public"],
            "hospitality": bldg_categories["hospitality"],
            "avg_floors_nearby": avg_levels,
            "max_floors_nearby": max_levels
        },
        "signal": {
            "is_urban": density > 200,
            "is_dense_urban": density > 500,
            "is_rural": density < 50,
            "has_high_rise_context": (max_levels or 0) >= 8,
            "mixed_use_area": bldg_categories["commercial"] > 10 and bldg_categories["residential"] > 20
        },
        "summary": f"""Area has {len(buildings)} mapped buildings within {radius_m}m ({density} per km²). Residential: {bldg_categories['residential']}, Commercial: {bldg_categories['commercial']}, Industrial: {bldg_categories['industrial']}. Nearby buildings average {avg_levels or 'unknown'} floors, max {max_levels or 'unknown'}.""",
        "confidence": "medium",
        "reasoning_hints": [
            f"Building density of {density}/km² indicates {'urban' if density > 200 else 'suburban' if density > 50 else 'rural'} character",
            f"{'Mixed-use zone with commercial activity' if bldg_categories['commercial'] > 10 and bldg_categories['residential'] > 20 else 'Primarily residential zone'}",
            f"{'High-rise buildings nearby suggest tall construction is viable' if (max_levels or 0) >= 8 else 'Low-rise context — tall building may stand out'}"
        ]
    }

    # ==================== FLOOD RISK ====================
    rivers = [w for w in water if w["tags"].get("waterway") == "river"]
    canals = [w for w in water if w["tags"].get("waterway") in ["canal","drain"]]
    wetlands = [w for w in water if w["tags"].get("wetland")]
    coastlines = [w for w in water if w["tags"].get("natural") == "coastline"]

    nr_dist, nr_item = nearest(rivers, lat, lng)
    nc_dist, nc_item = nearest(canals, lat, lng)
    nw_dist, nw_item = nearest(wetlands, lat, lng)
    ncoast_dist, ncoast_item = nearest(coastlines, lat, lng)
    nwater_dist, nwater_item = nearest(water, lat, lng)

    flood_score = 0
    if nwater_dist and nwater_dist < 100: flood_score += 4
    elif nwater_dist and nwater_dist < 500: flood_score += 2
    if len(wetlands) > 0: flood_score += 2
    if len(rivers) > 0: flood_score += 1
    if ncoast_dist and ncoast_dist < 500: flood_score += 2
    flood_score = min(flood_score, 10)

    param_flood_risk = {
        "parameter_name": "Flood Risk — Water Proximity",
        "value": {
            "flood_proxy_score": flood_score,
            "scale": "0-10",
            "nearest_river_m": nr_dist,
            "nearest_canal_m": nc_dist,
            "nearest_wetland_m": nw_dist,
            "nearest_any_water_m": nwater_dist,
            "coastline_proximity_m": ncoast_dist,
            "river_count": len(rivers),
            "canal_count": len(canals),
            "wetland_count": len(wetlands)
        },
        "signal": {
            "high_water_proximity": flood_score >= 6,
            "on_riverbank": nr_dist is not None and nr_dist < 200,
            "on_coast": ncoast_dist is not None and ncoast_dist < 500,
            "in_wetland_zone": len(wetlands) > 0,
            "drainage_nearby": nc_dist is not None and nc_dist < 300
        },
        "summary": f"""Water proximity score: {flood_score}/10. {'Very close to water bodies' if flood_score >= 6 else 'Moderate water proximity' if flood_score >= 3 else 'Limited water proximity'}. Nearest river: {nr_dist or 'unknown'}m. Nearest canal: {nc_dist or 'unknown'}m. {'Coastal zone' if ncoast_dist and ncoast_dist < 1000 else 'Inland location'}. {len(wetlands)} wetland areas nearby.""",
        "confidence": "medium",
        "reasoning_hints": [
            f"{'Multiple water bodies nearby — elevated design recommended' if len(water) > 5 else 'Limited mapped water bodies in radius'}",
            f"{'Within 200m of river — verify flood history' if nr_dist and nr_dist < 200 else 'Not immediately adjacent to river'}",
            f"{'Coastal construction considerations apply' if ncoast_dist and ncoast_dist < 500 else 'No coastal proximity from OSM'}"
        ]
    }

    # ==================== ROADS ====================
    road_types = {}
    major_count, minor_count = 0, 0
    nearest_road_dist, nearest_major_dist = float('inf'), float('inf')
    nearest_road_item, nearest_major_item = None, None

    for r in roads:
        rt = r["tags"].get("highway", "unclassified")
        road_types[rt] = road_types.get(rt, 0) + 1
        if rt in ["motorway","trunk","primary","secondary"]:
            major_count += 1
        else:
            minor_count += 1
        rlat, rlng = r.get("lat"), r.get("lon")
        if rlat and rlng:
            d = dist_m(lat, lng, rlat, rlng)
            if d < nearest_road_dist:
                nearest_road_dist, nearest_road_item = d, r
            if rt in ["motorway","trunk","primary","secondary"] and d < nearest_major_dist:
                nearest_major_dist, nearest_major_item = d, r

    nr_dist = round(nearest_road_dist, 1) if nearest_road_dist != float('inf') else None
    nmr_dist = round(nearest_major_dist, 1) if nearest_major_dist != float('inf') else None

    param_roads = {
        "parameter_name": "Road Network & Accessibility",
        "value": {
            "total_roads": len(roads),
            "major_roads": major_count,
            "minor_roads": minor_count,
            "road_density_per_sqkm": round(len(roads) / area_sqkm, 1),
            "nearest_road_m": nr_dist,
            "nearest_major_road_m": nmr_dist,
            "nearest_road_name": nearest_road_item["tags"].get("name") if nearest_road_item else None,
            "nearest_road_type": nearest_road_item["tags"].get("highway") if nearest_road_item else None,
            "road_types": road_types
        },
        "signal": {
            "has_direct_road_access": nr_dist is not None and nr_dist < 100,
            "has_major_road_access": nmr_dist is not None and nmr_dist < 500,
            "well_connected": len(roads) > 100,
            "isolated": len(roads) < 20,
            "urban_road_grid": major_count > 5 and minor_count > 50
        },
        "summary": f"""{len(roads)} roads mapped ({major_count} major, {minor_count} minor). Nearest road: {nr_dist or 'unknown'}m ({nearest_road_item['tags'].get('name','unnamed') if nearest_road_item else 'unknown'}). Nearest major road: {nmr_dist or 'unknown'}m. {'Well-connected area' if len(roads) > 100 else 'Moderate connectivity' if len(roads) > 30 else 'Limited road access'}.""",
        "confidence": "medium",
        "reasoning_hints": [
            f"{'Direct road frontage available' if nr_dist and nr_dist < 100 else 'Road access within ' + str(int(nr_dist)) + 'm' if nr_dist else 'Road access not determined'}",
            f"{'Connected to major road network' if nmr_dist and nmr_dist < 500 else 'May require access road development'}",
            f"{'Dense urban road grid' if len(roads) > 100 else 'Sparse road network'}"
        ]
    }

    # ==================== ECONOMIC ACTIVITY ====================
    amenity_types = {}
    edu = health = food = retail = finance = transport = gov = 0
    for a in amenities:
        at = a["tags"].get("amenity", "")
        amenity_types[at] = amenity_types.get(at, 0) + 1
        if at in ["school","college","university","kindergarten","library"]: edu += 1
        elif at in ["hospital","clinic","pharmacy","doctors","dentist"]: health += 1
        elif at in ["restaurant","cafe","fast_food","bar","pub","food_court"]: food += 1
        elif at in ["marketplace","fuel"]: retail += 1
        elif at in ["bank","atm","bureau_de_change"]: finance += 1
        elif at in ["bus_station","parking","taxi_stand","fuel"]: transport += 1
        elif at in ["townhall","courthouse","embassy","government"]: gov += 1

    shop_types = {}
    for s in shops:
        st = s["tags"].get("shop", "")
        shop_types[st] = shop_types.get(st, 0) + 1

    total_commercial = len(amenities) + len(shops)

    param_economic = {
        "parameter_name": "Economic Activity & Commercial Density",
        "value": {
            "total_amenities": len(amenities),
            "total_shops": len(shops),
            "commercial_pois_per_sqkm": round(total_commercial / area_sqkm, 1),
            "breakdown": {
                "education": edu,
                "healthcare": health,
                "food_beverage": food,
                "retail_services": retail,
                "finance": finance,
                "transport": transport,
                "government": gov
            },
            "top_amenity_types": dict(sorted(amenity_types.items(), key=lambda x: -x[1])[:5]),
            "top_shop_types": dict(sorted(shop_types.items(), key=lambda x: -x[1])[:5])
        },
        "signal": {
            "commercially_vibrant": total_commercial > 50,
            "has_basic_services": edu > 0 and health > 0 and food > 0,
            "has_banking": finance > 0,
            "has_public_transport": transport > 0,
            "retail_desert": total_commercial < 10
        },
        "summary": f"""{total_commercial} commercial POIs mapped ({len(amenities)} amenities + {len(shops)} shops). Density: {round(total_commercial/area_sqkm,1)} per km² — {'commercially active' if total_commercial > 50 else 'moderate activity' if total_commercial > 15 else 'limited commercial activity'}. Services: {edu} education, {health} healthcare, {food} food outlets, {finance} banking.""",
        "confidence": "medium",
        "reasoning_hints": [
            f"{'High commercial density indicates economic vibrancy' if total_commercial > 50 else 'Low commercial density may indicate limited local demand'}",
            f"{'Basic services available in area' if edu > 0 and health > 0 else 'Basic services gap noted'}",
            f"{'Banking access present' if finance > 0 else 'No banking POIs mapped nearby'}"
        ]
    }

    # ==================== TOURISM ====================
    tour_types = {}
    hotels = attractions = viewpoints = historic = beaches = 0
    for t in tourism:
        tt = t["tags"].get("tourism", "")
        tour_types[tt] = tour_types.get(tt, 0) + 1
        if tt == "hotel": hotels += 1
        elif tt == "attraction": attractions += 1
        elif tt == "viewpoint": viewpoints += 1
        elif tt == "historic": historic += 1
        elif tt == "beach": beaches += 1

    param_tourism = {
        "parameter_name": "Tourism & Hospitality Potential",
        "value": {
            "total_tourism_pois": len(tourism),
            "hotels_nearby": hotels,
            "attractions_nearby": attractions,
            "viewpoints_nearby": viewpoints,
            "historic_sites_nearby": historic,
            "beaches_nearby": beaches,
            "tourism_types": tour_types
        },
        "signal": {
            "is_tourist_area": len(tourism) > 5,
            "has_hotel_competition": hotels > 2,
            "has_attractions": attractions > 0 or viewpoints > 0,
            "heritage_zone": historic > 0,
            "coastal_tourism": beaches > 0
        },
        "summary": f"""{len(tourism)} tourism-related features mapped. {hotels} hotels, {attractions} attractions, {historic} historic sites, {beaches} beaches nearby. {'Established tourist zone' if len(tourism) > 5 else 'Limited tourism infrastructure' if len(tourism) > 0 else 'No tourism POIs mapped'}.""",
        "confidence": "medium",
        "reasoning_hints": [
            f"{'Tourism demand likely — ' + str(hotels) + ' hotels nearby' if hotels > 0 else 'No competing hotels mapped'}",
            f"{'Attractions nearby may drive footfall' if attractions > 0 else 'No major attractions mapped'}",
            f"{'Heritage considerations may apply' if historic > 0 else 'No heritage sites nearby'}"
        ]
    }

    # ==================== LAND USE ====================
    lu_types = {}
    for l in landuse:
        lt = l["tags"].get("landuse", "")
        lu_types[lt] = lu_types.get(lt, 0) + 1

    total_lu = len(landuse)
    param_landuse = {
        "parameter_name": "Land Use & Environmental Context",
        "value": {
            "mapped_landuse_zones": total_lu,
            "landuse_breakdown": lu_types,
            "residential_zones": lu_types.get("residential", 0),
            "commercial_zones": lu_types.get("commercial", 0),
            "industrial_zones": lu_types.get("industrial", 0),
            "agricultural_zones": lu_types.get("farmland", 0) + lu_types.get("orchard", 0),
            "forest_zones": lu_types.get("forest", 0) + lu_types.get("wood", 0)
        },
        "signal": {
            "residential_dominant": lu_types.get("residential", 0) > lu_types.get("commercial", 0),
            "industrial_nearby": lu_types.get("industrial", 0) > 0,
            "agricultural_buffer": lu_types.get("farmland", 0) > 0,
            "forest_nearby": lu_types.get("forest", 0) > 0,
            "mixed_zoning": len(lu_types) > 2
        },
        "summary": f"""{total_lu} land use zones mapped. {lu_types.get('residential',0)} residential, {lu_types.get('commercial',0)} commercial, {lu_types.get('industrial',0)} industrial. {lu_types.get('farmland',0)} farmland, {lu_types.get('forest',0)} forest areas nearby. {'Mixed-use zone' if len(lu_types) > 2 else 'Homogeneous land use'}.""",
        "confidence": "low",
        "reasoning_hints": [
            f"{'Residential zone — consider community impact' if lu_types.get('residential',0) > 0 else 'Non-residential zone'}",
            f"{'Industrial proximity noted' if lu_types.get('industrial',0) > 0 else 'No industrial zones mapped'}",
            f"{'Agricultural land nearby' if lu_types.get('farmland',0) > 0 else 'No farmland buffer'}"
        ]
    }

    # ==================== COMPARABLES ====================
    apartments = bldg_types.get("apartments", 0) + bldg_types.get("residential", 0)
    hotels_bldg = bldg_types.get("hotel", 0)
    commercial_bldg = bldg_types.get("commercial", 0) + bldg_types.get("office", 0) + bldg_types.get("retail", 0)

    param_comparables = {
        "parameter_name": "Comparable Projects Nearby",
        "value": {
            "similar_buildings": {
                "apartments": apartments,
                "hotels": hotels_bldg,
                "commercial_buildings": commercial_bldg,
                "total": len(buildings)
            },
            "building_height_context": {
                "avg_floors_nearby": avg_levels,
                "max_floors_nearby": max_levels,
                "buildings_with_height_data": len(levels_list)
            }
        },
        "signal": {
            "market_exists": apartments > 5 or hotels_bldg > 2 or commercial_bldg > 5,
            "oversupply_risk": apartments > 50 and avg_levels is None,
            "first_mover_advantage": apartments < 3 and hotels_bldg < 1
        },
        "summary": f"""{apartments} apartment/residential buildings, {hotels_bldg} hotels, {commercial_bldg} commercial buildings nearby. Nearby buildings average {avg_levels or 'unknown'} floors. {'Established market' if apartments > 5 or hotels_bldg > 2 else 'Thin market — limited comparable projects'}.""",
        "confidence": "medium",
        "reasoning_hints": [
            f"{'Comparable projects exist in vicinity' if apartments > 5 else 'Few comparable projects — market unproven'}",
            f"{'Height context: ' + str(avg_levels) + ' floors average' if avg_levels else 'No height data for nearby buildings'}",
            f"{'Potential oversupply if many generic buildings' if apartments > 50 else 'Limited supply in immediate area'}"
        ]
    }

    # ==================== POWER ====================
    substations = [p for p in power if p["tags"].get("power") == "substation"]
    towers = [p for p in power if p["tags"].get("power") == "tower"]
    poles = [p for p in power if p["tags"].get("power") == "pole"]
    ns_dist, _ = nearest(substations, lat, lng)

    param_power = {
        "parameter_name": "Power Infrastructure",
        "value": {
            "substations_nearby": len(substations),
            "power_towers_nearby": len(towers),
            "power_poles_nearby": len(poles),
            "nearest_substation_m": ns_dist
        },
        "signal": {
            "grid_accessible": len(substations) > 0 or len(towers) > 0,
            "substation_nearby": ns_dist is not None and ns_dist < 1000
        },
        "summary": f"""{len(substations)} substations, {len(towers)} towers, {len(poles)} poles mapped. Nearest substation: {ns_dist or 'unknown'}m. {'Grid infrastructure present' if len(substations) > 0 or len(towers) > 0 else 'Limited mapped power infrastructure'}.""",
        "confidence": "low",
        "reasoning_hints": [
            f"{'Power grid accessible' if len(substations) > 0 else 'Power infrastructure not prominently mapped'}",
            f"{'Substation within 1km' if ns_dist and ns_dist < 1000 else 'Substation distance unknown or >1km'}"
        ]
    }

    # ==================== REASONING SIGNALS ====================
    positive = []
    negative = []
    caution = []

    if density > 200:
        positive.append("High building density suggests established demand")
    if total_commercial > 50:
        positive.append("Commercially active area with services")
    if nmr_dist and nmr_dist < 500:
        positive.append("Connected to major road network")
    if len(tourism) > 5:
        positive.append("Tourism infrastructure present")
    if bldg_categories["commercial"] > 10:
        positive.append("Mixed-use commercial context")
    if flood_score < 3:
        positive.append("Low water proximity — reduced flood concern from OSM")

    if flood_score >= 6:
        negative.append(f"High water proximity (score {flood_score}/10) — flood risk")
    if density < 50:
        negative.append("Very low building density — possible demand shortage")
    if total_commercial < 10:
        negative.append("Limited commercial activity")
    if len(roads) < 20:
        negative.append("Poor road connectivity")

    if ncoast_dist and ncoast_dist < 500:
        caution.append("Coastal proximity — verify coastal regulations")
    if len(wetlands) > 0:
        caution.append("Wetland nearby — environmental clearance may be needed")
    if lu_types.get("industrial", 0) > 0:
        caution.append("Industrial zone nearby — check air quality")
    if historic > 0:
        caution.append("Historic site nearby — heritage regulations may apply")

    # ==================== LLM CONTEXT BLOCK ====================
    llm_context = f"""LOCATION CONTEXT (OpenStreetMap)
Coordinates: ({lat}, {lng}) | Radius: {radius_m}m | Area: {area_sqkm} km²

URBAN CHARACTER:
- Building density: {density} buildings/km² ({'urban' if density > 200 else 'suburban' if density > 50 else 'rural'})
- Nearest major road: {nmr_dist or 'unknown'}m
- Commercial POI density: {round(total_commercial/area_sqkm,1)}/km²

WATER & FLOOD:
- Flood proxy score: {flood_score}/10
- Nearest river: {nr_dist or 'unknown'}m | Nearest canal: {nc_dist or 'unknown'}m
- Coastline: {ncoast_dist or 'not nearby'}m | Wetlands: {len(wetlands)}

MARKET CONTEXT:
- Similar buildings: {apartments} apartments, {hotels_bldg} hotels
- Average building height nearby: {avg_levels or 'unknown'} floors
- Tourism POIs: {len(tourism)} ({hotels} hotels, {attractions} attractions)

KEY SIGNALS:
{chr(10).join(['+ ' + s for s in positive]) if positive else 'No strong positive signals from OSM'}
{chr(10).join(['- ' + s for s in negative]) if negative else ''}
{chr(10).join(['! ' + s for s in caution]) if caution else ''}""".strip()

    # ==================== FINAL ASSEMBLY ====================
    return {
        "schema_version": "2.1.0-ai-clean",
        "data_source": "OpenStreetMap",
        "overview": {
            "location": {"lat": lat, "lng": lng, "radius_m": radius_m, "area_sqkm": area_sqkm},
            "fetch_stats": {
                "total_osm_elements": len(elements),
                "buildings": len(buildings),
                "roads": len(roads),
                "water_bodies": len(water),
                "amenities": len(amenities),
                "shops": len(shops),
                "tourism_pois": len(tourism),
                "landuse_zones": len(landuse),
                "fetch_time_ms": int((time.time() - start) * 1000),
                "timestamp": datetime.now().isoformat()
            },
            "data_quality": {
                "osm_coverage_estimate": "good" if len(buildings) > 50 else "sparse" if len(buildings) > 10 else "very_sparse",
                "confidence_tier": "medium"
            }
        },
        "parameters": {
            "building_density": param_building_density,
            "flood_risk_water_proximity": param_flood_risk,
            "road_connectivity": param_roads,
            "economic_activity": param_economic,
            "tourism_potential": param_tourism,
            "land_use_environment": param_landuse,
            "comparable_projects": param_comparables,
            "power_infrastructure": param_power
        },
        "reasoning_signals": {
            "positive_indicators": positive,
            "negative_indicators": negative,
            "caution_flags": caution
        },
        "llm_context_block": llm_context
    }


def _describe_error(exc: Exception) -> str:
    if isinstance(exc, requests.exceptions.HTTPError) and exc.response is not None:
        status = exc.response.status_code
        label = requests.status_codes._codes.get(status, [""])[0].replace("_", " ").title()
        return f"HTTP {status} ({label})"
    if isinstance(exc, requests.exceptions.ConnectionError):
        return f"Connection error ({type(exc).__name__})"
    if isinstance(exc, requests.exceptions.Timeout):
        return "Request timed out"
    if isinstance(exc, ValueError) and "JSON" in str(exc):
        return f"Invalid JSON response ({exc})"
    return f"{type(exc).__name__}: {exc}"


class AIFriendlyOSMFetcher:
    def __init__(self, rate_limit: float = RATE_LIMIT):
        self.rate_limit = rate_limit
        self.last_request = 0

    def _wait(self):
        elapsed = time.time() - self.last_request
        if elapsed < self.rate_limit:
            time.sleep(self.rate_limit - elapsed)
        self.last_request = time.time()

    def fetch(self, lat: float, lng: float, radius_m: int = 1000) -> Dict:
        self._wait()

        query = f"""
        [out:json][timeout:60];
        (
          way["building"](around:{radius_m},{lat},{lng});
          relation["building"](around:{radius_m},{lat},{lng});
          way["highway"](around:{radius_m},{lat},{lng});
          way["waterway"](around:{radius_m},{lat},{lng});
          way["natural"="water"](around:{radius_m},{lat},{lng});
          relation["natural"="water"](around:{radius_m},{lat},{lng});
          way["water"](around:{radius_m},{lat},{lng});
          way["wetland"](around:{radius_m},{lat},{lng});
          relation["wetland"](around:{radius_m},{lat},{lng});
          way["natural"="coastline"](around:{radius_m},{lat},{lng});
          node["amenity"](around:{radius_m},{lat},{lng});
          way["amenity"](around:{radius_m},{lat},{lng});
          node["shop"](around:{radius_m},{lat},{lng});
          way["shop"](around:{radius_m},{lat},{lng});
          node["tourism"](around:{radius_m},{lat},{lng});
          way["tourism"](around:{radius_m},{lat},{lng});
          relation["tourism"](around:{radius_m},{lat},{lng});
          way["landuse"](around:{radius_m},{lat},{lng});
          relation["landuse"](around:{radius_m},{lat},{lng});
          node["power"="substation"](around:{radius_m},{lat},{lng});
          way["power"="substation"](around:{radius_m},{lat},{lng});
          node["power"="tower"](around:{radius_m},{lat},{lng});
          node["power"="pole"](around:{radius_m},{lat},{lng});
        );
        out body;
        >;
        out skel qt;
        """

        summaries = []
        for url in [OVERPASS_URL, OVERPASS_BACKUP]:
            endpoint_failures = []
            for attempt in range(1, MAX_ATTEMPTS + 1):
                logger.info("Overpass attempt %d/%d -> %s", attempt, MAX_ATTEMPTS, url)
                start = time.time()
                try:
                    resp = requests.post(url, data={"data": query}, timeout=REQUEST_TIMEOUT,
                                         headers={"User-Agent": "KeralaLandUseAgent/2.1-Clean"})
                    resp.raise_for_status()
                    raw = resp.json()
                    remark = raw.get("osm3s", {}).get("remark", "")
                    if remark and any(h in remark.lower() for h in TIMEOUT_REMARK_HINTS):
                        raise RuntimeError(f"Overpass returned remark: {remark}")
                    elapsed = time.time() - start
                    elements = raw.get("elements", [])
                    logger.info(
                        "OSM OK in %.1fs: %d elements (endpoint: %s, attempts: %d)",
                        elapsed, len(elements), url.rsplit("/", 2)[-2], attempt,
                    )
                    logger.debug("Element breakdown: %d ways, %d nodes, %d relations",
                                 sum(1 for e in elements if e.get("type") == "way"),
                                 sum(1 for e in elements if e.get("type") == "node"),
                                 sum(1 for e in elements if e.get("type") == "relation"))
                    return build_ai_output(raw, lat, lng, radius_m)
                except Exception as e:
                    elapsed = time.time() - start
                    reason = _describe_error(e)
                    endpoint_failures.append(reason)
                    attempts_left = MAX_ATTEMPTS - attempt
                    if attempts_left > 0:
                        backoff = min(RETRY_BACKOFF_BASE ** attempt, RETRY_BACKOFF_MAX)
                        logger.info(
                            "Attempt %d/%d failed after %.1fs: %s - retrying in %ds (%d attempt%s left on %s)",
                            attempt, MAX_ATTEMPTS, elapsed, reason, backoff, attempts_left,
                            "s" if attempts_left != 1 else "", url,
                        )
                        time.sleep(backoff)
                    else:
                        logger.warning(
                            "Endpoint exhausted after %d attempts: %s (failures: %s)",
                            MAX_ATTEMPTS, url, "; ".join(endpoint_failures),
                        )
            summaries.append(f"{url}: {'; '.join(endpoint_failures)}")
        raise ConnectionError("All Overpass endpoints failed. " + " | ".join(summaries))


def main():
    parser = argparse.ArgumentParser(description="AI-Friendly OSM Fetcher for Kerala Land-Use Agent")
    parser.add_argument("--lat", type=float, required=True)
    parser.add_argument("--lng", type=float, required=True)
    parser.add_argument("--radius", type=int, default=1000)
    parser.add_argument("--save", type=str, help="Save to file")
    parser.add_argument("--context-only", action="store_true", help="Output only LLM context block")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable DEBUG-level console output")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    fetcher = AIFriendlyOSMFetcher()
    result = fetcher.fetch(args.lat, args.lng, args.radius)

    if args.context_only:
        output = {"llm_context_block": result["llm_context_block"]}
    else:
        output = result

    print(json.dumps(output, indent=2, ensure_ascii=False))

    if args.save:
        with open(args.save, "w", encoding="utf-8") as f:
            json.dump(output, f, indent=2, ensure_ascii=False)
        logger.info(f"Saved to {args.save}")

if __name__ == "__main__":
    main()
