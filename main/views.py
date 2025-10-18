from django.http import JsonResponse, HttpRequest
from django.shortcuts import render
import json
from django.views.decorators.http import require_GET
from .services import diving_game_service, animal_map_service , animal_cards_service, future_family_safety_service, home_service, pollution_sources_service
from .services.animal_cards_service import fetch_kids_cards, build_collect_cards_json
from .services.about_water_sanitation_service import get_about_content
from .services.explore_water_quality_service import (
    list_gw_suburbs, list_sw_water_bodies, list_sw_locations,
    predict_groundwater, predict_surface
)

from .services.future_family_safety_service import list_sites, health_payload

# Create your views here.
def home(request):
    return render(request, 'water_home.html')

def about_water_sanitation(request):
    ctx = {"about": get_about_content()}
    return render(request, "about_water_sanitation.html", ctx)

def explore_water_quality(request):
    return render(request, "explore_water_quality.html")

#--------------------------------------------------------------------
# Ranjana - 15/09/2025
# Ranjana - 18/09/2025  views.py (update animal_map)
# Kevin - 24/09/2025 map revise

# Ranjana - 12/10/2025 new sightings.csv and insert the new CSV into db
# Kevin - 12/10/2025 update the back-end service and adjust the front-end

from django.contrib.staticfiles import finders
from django.templatetags.static import static

from functools import lru_cache
from django.core.cache import cache

from .services.animal_map_service import (
    get_all_sightings_dict,
    rebuild_sightings_json_from_db,  # New: manual rebuild trigger
    clear_sightings_cache,           # Optional: clear cache before rebuild
)

import os
import re


# --- Optional: memoize icon resolution to avoid repeated static-finder scans per sighting ---
@lru_cache(maxsize=512)
def _resolve_icon_url(common_name: str) -> str:
    """
    Return a static URL for an icon under static/sea-animal/*.png.
    Tries several filename variants (space/underscore/dash/lowercase); falls back to default.png.
    """
    if not common_name:
        candidates = ["sea-animal/default.png"]
    else:
        base = common_name.strip()
        lower = base.lower()
        candidates = [
            f"sea-animal/{base}.png",
            f"sea-animal/{base.replace(' ', '_')}.png",
            f"sea-animal/{base.replace(' ', '-')}.png",
            f"sea-animal/{lower}.png",
            f"sea-animal/{lower.replace(' ', '_')}.png",
            f"sea-animal/{lower.replace(' ', '-')}.png",
            f"sea-animal/{re.sub(r'[_-]+', ' ', lower)}.png",
            f"sea-animal/{re.sub(r'[_\\s]+', '-', lower)}.png",
            f"sea-animal/{re.sub(r'[-\\s]+', '_', lower)}.png",
            "sea-animal/default.png",
        ]

    for rel in candidates:
        if finders.find(rel):
            return static(rel)
    return static("sea-animal/default.png")


# --- Optional: cache the whole gallery list to avoid filesystem scans on every request ---
_GALLERY_CACHE_KEY = "animal_gallery_all_pngs"
_GALLERY_TTL = 60 * 60 * 24  # 24h

def _scan_gallery_from_static():
    """
    Scan collected static files and build a full gallery from sea-animal/*.png.
    This shows ALL species images that exist in static, not just those with sightings.
    """
    cached = cache.get(_GALLERY_CACHE_KEY)
    if cached is not None:
        return cached

    results = {}
    for f in finders.get_finders():
        for path, storage in getattr(f, "list", lambda *a, **k: [])([]):
            if not path.lower().startswith("sea-animal/"):
                continue
            if not path.lower().endswith(".png"):
                continue

            filename = os.path.basename(path)
            name_no_ext = os.path.splitext(filename)[0]

            # Convert file name to display name: underscores/dashes -> spaces; title-case if all lowercase
            display_name = re.sub(r"[_-]+", " ", name_no_ext).strip()
            if display_name.islower():
                display_name = display_name.title()

            icon_url = static(path)
            results.setdefault(display_name, {
                "common_name": display_name,
                "icon_url": icon_url,
            })

    gallery = [results[k] for k in sorted(results.keys(), key=lambda s: s.lower())]
    cache.set(_GALLERY_CACHE_KEY, gallery, _GALLERY_TTL)
    return gallery


def animal_map(request):
    """
    Render the page shell. The map and markers are drawn on the client side
    by fetching /animal_map/data (JSON).
    """
    return render(request, "animal_map.html")


def animal_map_data(request):
    """
    JSON endpoint for sightings + full gallery.
    Keep original variable names for center/bounds to avoid breaking JS.

    Optional query params:
      - ?rebuild=1         : force-rebuild AnimalSighting.json from DB (admin/debug use)
      - ?refresh_gallery=1 : clear cached gallery and rescan static
    """
    # --- Optional: on-demand JSON rebuild (admin/debug) ---
    if request.GET.get("rebuild") == "1":
        # Clear caches first to avoid stale data, then force rebuild
        clear_sightings_cache()
        rebuild_sightings_json_from_db()

    # --- Optional: refresh gallery cache and icon LRU ---
    if request.GET.get("refresh_gallery") == "1":
        cache.delete(_GALLERY_CACHE_KEY)
        _resolve_icon_url.cache_clear()

    # --- keep these names as requested ---
    victoria_coords = (-37.4713, 144.7852)
    victoria_bounds = [(-39.2, 140.9), (-33.9, 150.0)]

    raw = get_all_sightings_dict()  # list[dict]: sighting_id, latitude, longitude, common_name, size_text, comparison


    items = []
    first_coords_by_name = {}

    for s in raw:
        lat = s.get("latitude")
        lon = s.get("longitude")
        name = (s.get("common_name") or "Unknown").strip()

        # NEW: read new fields (with safe defaults)
        size_text = (s.get("size_text") or "").strip()
        comparison = (s.get("comparison") or "").strip()

        try:
            lat = float(lat)
            lon = float(lon)
        except (TypeError, ValueError):
            continue

        if name in first_coords_by_name:
            continue

        icon_url = _resolve_icon_url(name)

        # NEW: safe popup text with escaping; show size/comparison if present
        info_line = " · ".join([t for t in (size_text, comparison) if t])
        popup_html = f"<strong>{escape(name)}</strong>"
        if info_line:
            popup_html += f"<br><small>{escape(info_line)}</small>"

        items.append({
            "id": s.get("sighting_id"),
            "latitude": lat,
            "longitude": lon,
            "common_name": name,
            "size_text": size_text,         # NEW
            "comparison": comparison,       # NEW
            "icon_url": icon_url,
            "popup_html": f"<strong>{name}</strong>",
        })
        first_coords_by_name.setdefault(name, (lat, lon))

    gallery = _scan_gallery_from_static()

    payload = {
        "meta": {
            "victoria_coords": list(victoria_coords),
            "victoria_bounds": [list(victoria_bounds[0]), list(victoria_bounds[1])],
            "tile_url": "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
            "tile_attribution": "© OpenStreetMap contributors",
        },
        "items": items,
        "gallery": gallery,
        "first_coords_by_name": {k: list(v) for k, v in first_coords_by_name.items()},
    }

    resp = JsonResponse(payload, json_dumps_params={"ensure_ascii": False})
    resp["Cache-Control"] = "public, max-age=300"  # light browser cache (5 min)
    return resp

#--------------------------------------------------------------------
"""
def future_family_safety(request):
    return render(request, "future_family_safety.html")

"""
def future_family_safety(request):
    site_id = (request.GET.get("site_id") or "").strip()
    try:
        horizon_days = max(1, int(request.GET.get("horizon_days", "2")))
    except Exception:
        horizon_days = 2

    ctx = {
        "site_id": site_id,
        "horizon_days": horizon_days,
        "site_options": list_sites(),
    }
    if site_id:
        ctx["result"] = predict_surface(site_id=site_id, horizon_days=horizon_days)

    return render(request, "future_family_safety.html", ctx)


def api_sites(request: HttpRequest):
    try:
        return JsonResponse({"sites": list_sites()})
    except Exception as e:
        return JsonResponse({"sites": [], "error": str(e)}, status=500)

def api_family_safety_forecast(request: HttpRequest):
    site_id = request.GET.get("site_id") or ""
    # accept either hours or days; 48h == 2 days
    h_hours = request.GET.get("h") or request.GET.get("hours")
    h_days = request.GET.get("d") or request.GET.get("days")

    if h_hours:
        try:
            import math
            horizon_days = max(1, math.ceil(int(h_hours) / 24))
        except Exception:
            horizon_days = 2
    elif h_days:
        try:
            horizon_days = max(1, int(h_days))
        except Exception:
            horizon_days = 2
    else:
        horizon_days = 2  # default for your 48h page

    if not site_id:
        return JsonResponse({"error": "missing site_id"}, status=400)

    try:
        result = predict_site(site_id=str(site_id), horizon_days=horizon_days)
        return JsonResponse(result)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)

# --------------------------------------------------------------------
# NEW: Health check endpoint
# --------------------------------------------------------------------
def healthz(request: HttpRequest):
    """Lightweight health check for Nginx/Docker/ELB probes."""
    return JsonResponse(health_payload())


#--------------------------------------------------------------------

def pollution_sources(request):
    return render(request, "pollution_sources.html")

"""
def animal_cards(request):
    return render(request, "for_kids_learn_play.html")
"""

def animal_cards(request):
    return render(request, "animal_cards.html", {"db_cards": fetch_kids_cards(),
                                                        "collect_cards_json": build_collect_cards_json(),})


# def animal_map(request):
#     return render(request, "animal_map.html")

#------------------------------JEWEL----------------------------------------

@require_GET
def api_gw_suburbs(request):
    try:
        q = (request.GET.get("q") or "").strip().lower()
        items = list_gw_suburbs()
        if q:
            items = [s for s in items if s.lower().startswith(q)]
        return JsonResponse({"items": [{"label": s, "value": s} for s in items]})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)

@require_GET
def api_sw_water_bodies(request):
    try:
        q = (request.GET.get("q") or "").strip().lower()
        items = list_sw_water_bodies()
        if q:
            items = [s for s in items if s.lower().startswith(q)]
        return JsonResponse({"items": [{"label": s, "value": s} for s in items]})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)

@require_GET
def api_sw_locations(request):
    try:
        water_body = (request.GET.get("water_body") or "").strip()
        if not water_body:
            return JsonResponse({"items": []})
        q = (request.GET.get("q") or "").strip().lower()
        items = list_sw_locations(water_body)
        if q:
            items = [s for s in items if s.lower().startswith(q)]
        return JsonResponse({"items": [{"label": s, "value": s} for s in items]})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)

# ---------------- NEW: Prediction endpoints ----------------
@require_GET
def api_quality_gw(request):
    suburb = (request.GET.get("suburb") or "").strip()
    date_str = (request.GET.get("date") or "").strip()
    if not suburb or not date_str:
        return JsonResponse({"error": "suburb and date are required"}, status=400)
    res = predict_groundwater(suburb=suburb, date_str=date_str)
    return JsonResponse(res, json_dumps_params={"ensure_ascii": False})

@require_GET
def api_quality_sw(request):
    water_body = (request.GET.get("water_body") or "").strip()
    location = (request.GET.get("location") or "").strip()
    date_str = (request.GET.get("date") or "").strip()
    if not water_body or not location or not date_str:
        return JsonResponse({"error": "water_body, location and date are required"}, status=400)
    res = predict_surface(water_body=water_body, location=location, date_str=date_str)
    return JsonResponse(res, json_dumps_params={"ensure_ascii": False})

#------------------------------------------------------------

def diving_game(request):
    return render(request, "diving_game.html")
