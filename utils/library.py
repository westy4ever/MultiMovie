# -*- coding: utf-8 -*-
"""
Favorites, history, and resume position management.
"""
import time
from .state import load_state, save_state

def entry_from_item(item, site, m_type, extra=None):
    entry = {
        "title": item.get("title", ""),
        "url": item.get("url", ""),
        "poster": item.get("poster") or item.get("image") or "",
        "plot": item.get("plot", ""),
        "year": item.get("year", ""),
        "rating": item.get("rating", ""),
        "type": item.get("type", "") or m_type,
        "_action": item.get("_action", "details"),
        "_site": item.get("_site", site),
        "_m_type": item.get("_m_type", m_type),
        "_saved_at": int(time.time()),
    }
    if extra:
        entry.update(extra)
    return entry

def upsert_library_item(bucket, entry, limit=100):
    state = load_state()
    items = state.setdefault(bucket, [])
    key = entry.get("url")
    # Preserve last_position_sec if present in old item
    if not entry.get("last_position_sec"):
        for old in items:
            if old.get("url") == key and old.get("last_position_sec"):
                entry["last_position_sec"] = old["last_position_sec"]
                break
    items = [i for i in items if i.get("url") != key]
    items.insert(0, entry)
    state[bucket] = items[:limit]
    save_state(state)

def toggle_favorite_entry(entry):
    state = load_state()
    favorites = state.setdefault("favorites", [])
    key = entry.get("url")
    for idx, item in enumerate(favorites):
        if item.get("url") == key:
            favorites.pop(idx)
            save_state(state)
            return False
    favorites.insert(0, entry)
    state["favorites"] = favorites[:100]
    save_state(state)
    return True

def is_favorite(url):
    return any(item.get("url") == url for item in (load_state().get("favorites") or []))

def history_items():
    return load_state().get("history") or []

def favorite_items():
    return load_state().get("favorites") or []

def get_saved_position(url):
    for item in (load_state().get("history") or []):
        if item.get("url") == url:
            pos = int(item.get("last_position_sec") or 0)
            return pos if pos > 30 else 0
    return 0

def save_position(url, seconds):
    seconds = int(seconds or 0)
    if 0 < seconds < 30:
        return
    state = load_state()
    for item in (state.get("history") or []):
        if item.get("url") == url:
            item["last_position_sec"] = seconds
            save_state(state)
            return