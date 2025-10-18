# animal_map_service.py

import json
from pathlib import Path
from django.core.cache import cache
from django.conf import settings
from ..models import AnimalSighting
from django.db import OperationalError, ProgrammingError


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

# --- bump schema version because JSON shape changed (added size_text, comparison) ---
SCHEMA_VERSION = "v2"

_USE_DB = getattr(settings, "USE_DB_FOR_SIGHTINGS", False)
_DB_ALIAS = getattr(settings, "SIGHTINGS_DB_ALIAS", "default") 


CACHE_KEY = f"all_sightings_json:{SCHEMA_VERSION}"
CACHE_TTL  = 60 * 60 * 24  # 24 hours
_REBUILD_LOCK_KEY  = CACHE_KEY + ":rebuild_lock"
_REBUILD_LOCK_TTL  = 30  # seconds


if getattr(settings, "SIGHTINGS_JSON_PATH", None):
    _JSON_PATH = Path(settings.SIGHTINGS_JSON_PATH)
else:
    _JSON_PATH = Path(__file__).with_name(f"AnimalSighting.{SCHEMA_VERSION}.json")


_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)

# In-process memo to avoid repeated disk I/O and JSON parsing within the same worker
# Shape: {"etag": "<mtime_ns-size>", "data": <list[dict]>}
_MEMO = {"etag": None, "data": None}


import os

def _write_json_atomic(path: Path, data) -> None:

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")

    try:
        if _fastjson:
            payload = _fastjson.dumps(
                data,
                option=_fastjson.OPT_INDENT_2 | _fastjson.OPT_NON_STR_KEYS
            )
            with open(tmp, "wb") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
        else:
            with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())

        os.replace(tmp, path)
        _dbg(f"Wrote JSON file atomically to {path}")
    except Exception as e:
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass
        raise



def _query_db_minimal():
    _dbg(f"Querying DB alias='{_DB_ALIAS}' via Django ORM (minimal fields)")
    qs = (AnimalSighting.objects
            .using(_DB_ALIAS)
            .values("sighting_id", "latitude", "longitude", "common_name",
                    "size_text", "comparison"))
    return list(qs)


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
    if not _USE_DB:
        _dbg("FORCE rebuild requested but USE_DB_FOR_SIGHTINGS=False; skip DB and keep file as-is")
        if _JSON_PATH.exists():
            data = _load_json_from_disk(_JSON_PATH)
        else:
            data = []
        cache.set(CACHE_KEY, data, timeout=CACHE_TTL)
        _MEMO.update({"etag": _file_etag(_JSON_PATH), "data": data})
        return data

    _dbg("FORCE rebuild requested: fetching from DB and rewriting JSON")
    try:
        data = _query_db_minimal()
    except (OperationalError, ProgrammingError) as e:
        _dbg(f"DB error during FORCE rebuild: {e!r}; will NOT crash; falling back to file/empty")
        data = _load_json_from_disk(_JSON_PATH) if _JSON_PATH.exists() else []

    _write_json_atomic(_JSON_PATH, data)
    new_etag = _file_etag(_JSON_PATH)
    _MEMO["etag"] = new_etag
    _MEMO["data"] = data
    cache.set(CACHE_KEY, data, timeout=CACHE_TTL)
    _dbg("Cache and memo refreshed after FORCE rebuild")
    return data



def get_all_sightings_dict():
    """
    Load sightings for the frontend. Order:
    1) Django cache
    2) Module memo (etag match) / JSON file
    3) If allowed, query DB and refresh JSON+cache (with safe fallback)
    """
    # 1) Cache
    data = cache.get(CACHE_KEY)
    if data is not None:
        _dbg("HIT: Django cache")
        return data
    _dbg("MISS: Django cache")

    # 2) Memo / JSON
    etag = _file_etag(_JSON_PATH)
    if etag and _MEMO["etag"] == etag and _MEMO["data"] is not None:
        _dbg("HIT: Module memo (etag matched JSON on disk)")
        data = _MEMO["data"]
        cache.set(CACHE_KEY, data, timeout=CACHE_TTL)
        return data

    if etag:
        try:
            data = _load_json_from_disk(_JSON_PATH)
            _dbg("HIT: JSON file on disk")
            _MEMO.update({"etag": etag, "data": data})
            cache.set(CACHE_KEY, data, timeout=CACHE_TTL)
            return data
        except Exception as e:
            _dbg(f"ERROR reading JSON file, will consider DB: {e!r}")


    if not _USE_DB:
        _dbg("DB fetch disabled (USE_DB_FOR_SIGHTINGS=False); serving empty or existing file")
        return []

    got_lock = cache.add(_REBUILD_LOCK_KEY, "1", timeout=_REBUILD_LOCK_TTL)
    if not got_lock:
        _dbg("Rebuild lock held; re-check cache")
        data = cache.get(CACHE_KEY)
        if data is not None:
            _dbg("HIT after waiting: Django cache")
            return data
        _dbg("Still MISS; proceeding to DB (rare)")

    try:
        data = _query_db_minimal()
    except (OperationalError, ProgrammingError) as e:
        _dbg(f"DB error on fetch: {e!r}; will fall back to file/empty without crashing")
        data = _load_json_from_disk(_JSON_PATH) if _JSON_PATH.exists() else []

    try:
        _write_json_atomic(_JSON_PATH, data)
        new_etag = _file_etag(_JSON_PATH)
        if new_etag:
            _MEMO.update({"etag": new_etag, "data": data})
    except Exception as e:
        _dbg(f"ERROR writing JSON file; serving data without on-disk cache: {e!r}")
        _MEMO.update({"etag": None, "data": None})

    cache.set(CACHE_KEY, data, timeout=CACHE_TTL)
    cache.delete(_REBUILD_LOCK_KEY)
    return data



def clear_sightings_cache():
    """Manual cache invalidation (also call from signals on save/delete)."""
    cache.delete(CACHE_KEY)
    cache.delete(_REBUILD_LOCK_KEY)
    _MEMO["etag"] = None
    _MEMO["data"] = None
    _dbg("Cleared Django cache, rebuild lock, and module memo")
