# animal_map_service.py

import json
from pathlib import Path
from django.core.cache import cache
from ..models import AnimalSighting

# Optional speed-up: prefer orjson if installed
try:
    import orjson as _fastjson
except Exception:
    _fastjson = None

# --- Debug toggle for I/O source prints ---
_DEBUG_IO = False

def _dbg(msg: str) -> None:
    if _DEBUG_IO:
        print(f"[animal_map_service] {msg}")

CACHE_KEY = "all_sightings_json"
CACHE_TTL = 60 * 60 * 24  # 24 hours

# Rebuild lock to prevent cache stampede (short TTL)
_REBUILD_LOCK_KEY = CACHE_KEY + ":rebuild_lock"
_REBUILD_LOCK_TTL = 30  # seconds

# JSON path in the same folder
_JSON_PATH = Path(__file__).with_name("AnimalSighting.json")

# In-process memo to avoid repeated disk I/O and JSON parsing within the same worker
# Shape: {"etag": "<mtime_ns-size>", "data": <list[dict]>}
_MEMO = {"etag": None, "data": None}


def _write_json_atomic(path: Path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    if _fastjson:
        payload = _fastjson.dumps(data, option=_fastjson.OPT_INDENT_2 | _fastjson.OPT_NON_STR_KEYS)
        tmp.write_bytes(payload)
    else:
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)
    _dbg(f"Wrote JSON file atomically to {path}")


def _query_db_minimal():
    # NOTE: keep keys consistent with your DB columns
    _dbg("Querying RDS via Django ORM (minimal fields)")
    return list(
        AnimalSighting.objects.values(
            "sighting_id", "latitude", "longitude", "common_name"
        )
    )


def _file_etag(p: Path) -> str | None:
    try:
        st = p.stat()
        return f"{st.st_mtime_ns}-{st.st_size}"
    except FileNotFoundError:
        return None


def _load_json_from_disk(p: Path):
    """Load and parse JSON file from disk using fast parser if available."""
    _dbg(f"Loading JSON from disk: {p}")
    if _fastjson:
        return _fastjson.loads(p.read_bytes())
    else:
        with p.open("r", encoding="utf-8") as f:
            return json.load(f)


def rebuild_sightings_json_from_db():
    """Force rebuild the on-disk JSON from DB and refresh cache/memo."""
    _dbg("FORCE rebuild requested: fetching from DB and rewriting JSON")
    data = _query_db_minimal()
    _write_json_atomic(_JSON_PATH, data)
    new_etag = _file_etag(_JSON_PATH)
    _MEMO["etag"] = new_etag
    _MEMO["data"] = data
    cache.set(CACHE_KEY, data, timeout=CACHE_TTL)
    _dbg("Cache and memo refreshed after FORCE rebuild")
    return data


def get_all_sightings_dict():
    """
    Return a list[dict] that is safe for JSON serialization.
    Keep only fields the frontend needs for the map.

    Read order:
    1) Try Django cache
    2) If cache miss, try module-level memo (etag check) and/or JSON file
    3) If JSON missing/invalid, query DB and refresh JSON + cache
    """
    # 1) Try process-external cache (Redis/Memcached/LocMem)
    data = cache.get(CACHE_KEY)
    if data is not None:
        _dbg("HIT: Django cache")
        return data
    _dbg("MISS: Django cache")

    # 2) Try JSON file via module-level memo to avoid repeated disk I/O in the same worker
    etag = _file_etag(_JSON_PATH)
    if etag and _MEMO["etag"] == etag and _MEMO["data"] is not None:
        _dbg("HIT: Module memo (etag matched JSON on disk)")
        data = _MEMO["data"]
        cache.set(CACHE_KEY, data, timeout=CACHE_TTL)
        _dbg("Refreshed Django cache from module memo")
        return data

    if etag:
        try:
            data = _load_json_from_disk(_JSON_PATH)
            _dbg("HIT: JSON file on disk")
            # update memo
            _MEMO["etag"] = etag
            _MEMO["data"] = data
            cache.set(CACHE_KEY, data, timeout=CACHE_TTL)
            _dbg("Refreshed Django cache from JSON file")
            return data
        except Exception as e:
            _dbg(f"ERROR reading JSON file, will fall back to DB: {e!r}")

    # 3) JSON not present or invalid => rebuild from DB
    got_lock = cache.add(_REBUILD_LOCK_KEY, "1", timeout=_REBUILD_LOCK_TTL)
    if not got_lock:
        _dbg("Rebuild lock held by another worker; re-checking cache")
        data = cache.get(CACHE_KEY)
        if data is not None:
            _dbg("HIT after waiting: Django cache")
            return data
        _dbg("Still MISS after waiting; proceeding to DB (rare case)")

    data = _query_db_minimal()
    try:
        _write_json_atomic(_JSON_PATH, data)
        new_etag = _file_etag(_JSON_PATH)
        if new_etag:
            _MEMO["etag"] = new_etag
            _MEMO["data"] = data
            _dbg("Updated module memo after writing JSON")
    except Exception as e:
        _dbg(f"ERROR writing JSON file; serving data without on-disk cache: {e!r}")
        _MEMO["etag"] = None
        _MEMO["data"] = None

    cache.set(CACHE_KEY, data, timeout=CACHE_TTL)
    _dbg("Refreshed Django cache from DB result")
    cache.delete(_REBUILD_LOCK_KEY)  # best-effort release
    return data


def clear_sightings_cache():
    """Manual cache invalidation (also call from signals on save/delete)."""
    cache.delete(CACHE_KEY)
    cache.delete(_REBUILD_LOCK_KEY)
    _MEMO["etag"] = None
    _MEMO["data"] = None
    _dbg("Cleared Django cache, rebuild lock, and module memo")
