from django.conf import settings
from django.core.cache import cache
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver

import os
import json
from ..models import KidsCard

try:
    from ..models import KidsCollectCard as _KidsCollectCard  # optional model
except Exception:
    _KidsCollectCard = None


# ===================== Cache settings =====================
# Works with any Django cache backend (LocMem/Redis/Memcached).
# Override in settings.py if needed:
#   KIDS_CACHE_SECONDS = 300
#   KIDS_CACHE_PREFIX  = "kids_cards"
#   KIDS_CACHE_VERSION = 1  # bump to invalidate all at once
KIDS_CACHE_SECONDS = int(getattr(settings, "KIDS_CACHE_SECONDS", 300))
KIDS_CACHE_PREFIX  = str(getattr(settings, "KIDS_CACHE_PREFIX", "kids_cards"))
KIDS_CACHE_VERSION = int(getattr(settings, "KIDS_CACHE_VERSION", 1))

def _ckey(name: str) -> str:
    """Build namespaced cache key with global version."""
    return f"{KIDS_CACHE_PREFIX}:v{KIDS_CACHE_VERSION}:{name}"

def _cache_get(name: str):
    try:
        return cache.get(_ckey(name))
    except Exception:
        return None

def _cache_set(name: str, value, timeout: int | None = None):
    try:
        cache.set(_ckey(name), value, timeout=timeout or KIDS_CACHE_SECONDS)
    except Exception:
        pass

def _cache_del(name: str):
    try:
        cache.delete(_ckey(name))
    except Exception:
        pass


# ===================== Switch here (unchanged) =====================
# True  -> always use local tuples (no DB/RDS calls)
# False -> always use database (with caching)
# None  -> fall back to USE_LOCAL_KIDS_CARDS or settings.DEBUG
LOCAL_CARDS_OVERRIDE = True
# ================================================================


# ---- Local fallback data (tuples -> dicts) ----
_LOCAL_KIDS_TUPLES =[
    (1, 'Water inside an elephant',
     '<p>About 70% of an elephant is water.</p>',
     '<p>This water helps with blood flow, cooling, and moving nutrients.</p>',
     'Tap me to unlock the card!', 5, 1),

    (2, 'How watery is a tomato?',
      '<p>About 95% of a tomato is water.</p>',
      '<p>That’s why tomatoes are so juicy.</p>',
      'Tap me to unlock the card!', 5, 0),

    (3, 'Water in our bodies',
     '<p>About 66% of the human body is water.</p>',
     '<p>It’s in our brain, blood, and organs.</p>',
     'Tap me to unlock the card!', 5, 0),

    (4, 'Longest water pipeline in Australia',
     '<p>The longest water supply pipeline is in Western Australia.</p>',
     '<p>It carries water long distances to drier areas.</p>',
     'Tap me to unlock the card!', 5, 0),

    (5, 'Where at home uses the most water?',
     '<p>The bathroom uses the most water at home.</p>',
     '<p>Short showers and turning off taps can save a lot.</p>',
     'Tap me to unlock the card!', 5, 0),

    (6, 'Who lacks safe drinking water?',
     '<p>About 1 in 4 people lack safe drinking water.</p>',
     '<p>Clean water projects help families stay healthy.</p>',
     'Tap me to unlock the card!', 5, 0),

    (7, 'Sea turtles and plastic',
     '<p>About 52% of sea turtles have eaten plastic.</p>',
     '<p>Plastic makes them sick and comes from pollution.</p>',
     'Tap me to unlock the card!', 5, 0),

    (8, 'Whale sharks and plastic',
     '<p>Whale shark may swallow about 137 pieces plastic every hour.</p>',
     '<p>They can’t tell plastic from food while filtering water.</p>',
     'Tap me to unlock the card!', 5, 0),

    (9, 'Sharks and mercury',
     '<p>About 25% of sharks have unsafe mercury levels.</p>',
     '<p>Pollution builds up in the fish they eat.</p>',
     'Tap me to unlock the card!', 5, 0)
]


def _as_dict(row):
    """Convert a tuple into a dict that matches Django .values() output."""
    return {
        "card_order": row[0],
        "title": row[1],
        "lead_html": row[2],
        "detail_html": row[3],
        "hint_text": row[4],
        "read_seconds": row[5],
        "is_starter": bool(row[6]),
    }

def _use_local_data() -> bool:
    """
    Decide whether to use local tuples instead of hitting the DB.
    Priority:
      1) LOCAL_CARDS_OVERRIDE (True/False/None)
      2) settings.USE_LOCAL_KIDS_CARDS (accepts "1/true/yes")
      3) settings.DEBUG
    """
    override = LOCAL_CARDS_OVERRIDE
    if override is not None:
        print(f"[kids_cards] decision: LOCAL_CARDS_OVERRIDE={override} -> use_local={bool(override)}")
        return bool(override)

    val = getattr(settings, "USE_LOCAL_KIDS_CARDS", None)
    if val is not None:
        parsed = (val.strip().lower() in {"1", "true", "t", "yes", "y"}) if isinstance(val, str) else bool(val)
        print(f"[kids_cards] decision: settings.USE_LOCAL_KIDS_CARDS={val!r} (parsed={parsed}) -> use_local={parsed}")
        return parsed

    debug = bool(getattr(settings, "DEBUG", False))
    print(f"[kids_cards] decision: settings.DEBUG={debug} -> use_local={debug}")
    return debug


def fetch_kids_cards():
    """
    Dev/local: return static JSON-like data, now cached as well.
    Production: query the DB (limit 9) with caching.
    """
    use_local = _use_local_data()

    # Use separate cache keys for local vs db to avoid mixing
    mode = "local" if use_local else "db"
    cache_key = f"kids_cards:list:limit9:mode={mode}"
    cached = _cache_get(cache_key)
    if cached is not None:
        print(f"[kids_cards] source=CACHE({mode}), returning={len(cached)} items (limit 9)")
        return cached

    if use_local:
        data = sorted((_as_dict(t) for t in _LOCAL_KIDS_TUPLES),
                      key=lambda d: d["card_order"])[:9]
        _cache_set(cache_key, data)
        print(f"[kids_cards] source=LOCAL tuples, returning={len(data)} items (limit 9)")
        return data

    qs = list(
        KidsCard.objects.order_by("card_order").values(
            "card_order", "title", "lead_html", "detail_html",
            "hint_text", "read_seconds", "is_starter"
        )[:9]
    )
    _cache_set(cache_key, qs)
    print(f"[kids_cards] source=DB, returning={len(qs)} items (limit 9)")
    return qs


# =========================
#  Collectible card data below
# =========================

# Local collectible-card tuples:
# (no, title, aria, img_relative, desc, special, levelCap, levelCur) ---> SS, S, A, B, C
_LOCAL_COLLECT_TUPLES = [
    (1, "Elephant", "Elephant card", "cards/elephant.gif",
     "The gentle giant of the land—family-centered, needs vast grasslands and plenty of water.", "B", 3, 3),

    (2, "Blue Whale", "Whale card", "cards/blue_whale.gif",
     "Largest animal on Earth, a gentle giant that filters tiny krill from the ocean.", "S", 3, 1),

    (3, "Great White Shark", "Great White Shark card", "cards/great_white.gif",
     "Apex ocean hunter—keen senses keep food webs balanced; needs clean, open coasts.", "S", 3, 3),

    (4, "Fin Whale", "Fin Whale card", "cards/fin_whale.gif",
     "The sleek ‘greyhound of the sea’—second largest whale, feeds on krill in vast oceans.", "A", 3, 3),

    (5, "Hammerhead Shark", "Hammerhead Shark card", "cards/hammerhead_shark.gif",
     "With its hammer-shaped head, it scans wide seas—needs healthy reefs to hunt and thrive.", "A", 3, 2),

    (6, "Orca", "Orca card", "cards/orca.gif",
     "The black-and-white apex hunter—social and smart, thrives in clean, rich seas.", "SS", 3, 1),

    (7, "Sea Turtle", "Sea Turtle card", "cards/sea_turtle.gif",
     "Ancient ocean traveler—returns to beaches to nest, needs clean seas and safe shores.", "S", 3, 2),

    (8, "Whale Shark", "Whale Shark card", "cards/whale_shark.gif",
     "Gentle giant of the sea—feeds on plankton, thrives in clean, warm oceans.", "A", 3, 3),

    (9, "Sharks", "Sharks card", "cards/sharks.gif",
     "From hammerheads to great whites—sharks come in many forms, all needing healthy oceans.", "SS", 3, 3),
]

def _static_url(path: str) -> str:
    """Return STATIC_URL-based path unless already absolute."""
    if not path:
        return ""
    if path.startswith("http://") or path.startswith("https://") or path.startswith("/"):
        return path
    base = getattr(settings, "STATIC_URL", "/static/")
    return f"{base}{path.lstrip('/')}"

def _collect_as_dict(row):
    """Convert a local collectible tuple into the shape expected by the front-end."""
    return {
        "no": row[0],
        "title": row[1],
        "aria": row[2],
        "img": _static_url(row[3]),
        "desc": row[4],
        "special": row[5],
        "levelCap": int(row[6]),
        "levelCur": int(row[7]),
    }

def fetch_collect_cards(limit: int | None = None):
    """
    Return a list[dict] for collectible cards.
    - If _use_local_data() is True, use _LOCAL_COLLECT_TUPLES (cached as well).
    - Otherwise, if the optional _KidsCollectCard model exists, query the DB with caching.
    """
    use_local = _use_local_data()
    items = []
    src = "LOCAL" if use_local else "DB"

    # Cache regardless of source (local/db) to avoid recomputing/encoding.
    mode = "local" if use_local or _KidsCollectCard is None else "db"
    cache_key = f"collect_cards:list:mode={mode}:all"
    cached = _cache_get(cache_key)
    if cached is not None:
        items = cached
        src = f"CACHE({mode})"
    else:
        if use_local or _KidsCollectCard is None:
            items = [_collect_as_dict(t) for t in _LOCAL_COLLECT_TUPLES]
        else:
            values = list(_KidsCollectCard.objects.order_by("no").values(
                "no", "title", "aria", "img", "image_url", "desc", "description",
                "special", "levelCap", "levelCur", "level_cap", "level_cur"
            ))
            for v in values:
                img = v.get("img") or v.get("image_url") or ""
                desc = v.get("desc") or v.get("description") or ""
                level_cap = v.get("levelCap", v.get("level_cap", 3))
                level_cur = v.get("levelCur", v.get("level_cur", 1))
                items.append({
                    "no": v.get("no"),
                    "title": v.get("title"),
                    "aria": v.get("aria") or f"{v.get('title', 'Card')} card",
                    "img": _static_url(img),
                    "desc": desc,
                    "special": v.get("special") or "S",
                    "levelCap": int(level_cap or 3),
                    "levelCur": int(level_cur or 1),
                })
        _cache_set(cache_key, items)

    if limit is not None and isinstance(limit, int) and limit > 0:
        items = items[:limit]

    print(f"[kids_cards] collect_cards source={src}, count={len(items)}")
    return items

def build_collect_cards_json(limit: int | None = None) -> str:
    """
    Produce a JSON string for the template's
    `<script id="collectCardsData" type="application/json">`.
    Cached separately by (optional) limit for both local and db modes.
    """
    mode = "local" if _use_local_data() or _KidsCollectCard is None else "db"
    cache_key = f"collect_cards:json:mode={mode}:{limit if limit is not None else 'all'}"

    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    data = fetch_collect_cards(limit=limit)
    s = json.dumps(data, ensure_ascii=False)
    _cache_set(cache_key, s)
    return s


# ===================== Automatic cache invalidation =====================
# When KidsCard or KidsCollectCard changes, clear relevant keys.
# For LOCAL mode: simply bump KIDS_CACHE_VERSION if tuples change.

def _invalidate_kids_cards_cache(*_args, **_kwargs):
    _cache_del("kids_cards:list:limit9:mode=local")
    _cache_del("kids_cards:list:limit9:mode=db")

def _invalidate_collect_cards_cache(*_args, **_kwargs):
    _cache_del("collect_cards:list:mode=local:all")
    _cache_del("collect_cards:list:mode=db:all")
    # remove JSON variants (both modes, common sizes)
    for mode in ("local", "db"):
        _cache_del(f"collect_cards:json:mode={mode}:all")
        for n in (1, 3, 6, 9, 12):
            _cache_del(f"collect_cards:json:mode={mode}:{n}")

@receiver(post_save, sender=KidsCard)
@receiver(post_delete, sender=KidsCard)
def _kids_cards_changed(sender, **kwargs):
    _invalidate_kids_cards_cache()

if _KidsCollectCard is not None:
    @receiver(post_save, sender=_KidsCollectCard)
    @receiver(post_delete, sender=_KidsCollectCard)
    def _kids_collect_changed(sender, **kwargs):
        _invalidate_collect_cards_cache()
