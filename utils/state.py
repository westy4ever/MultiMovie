# -*- coding: utf-8 -*-
"""
Persistent state (JSON) management.
"""
import os
import json
import sys

PLUGIN_PATH = os.path.dirname(os.path.dirname(__file__))
DEFAULT_OWNER = "MultiMovie User"
DEFAULT_TMDB_KEY = "01fd9e035ea1458748e99eb7216b0259"

_STATE_CACHE = None

def state_path():
    for candidate in (
        "/etc/enigma2/multimovie_state.json",
        os.path.join(PLUGIN_PATH, "multimovie_state.json"),
        "/tmp/multimovie_state.json"
    ):
        try:
            parent = os.path.dirname(candidate)
            if parent and os.path.isdir(parent) and os.access(parent, os.W_OK):
                return candidate
        except Exception:
            pass
    return "/tmp/multimovie_state.json"

def default_state():
    return {
        "config": {
            "owner": DEFAULT_OWNER,
            "tmdb_api_key": DEFAULT_TMDB_KEY,
        },
        "favorites": [],
        "history": [],
    }

def load_state():
    global _STATE_CACHE
    if _STATE_CACHE is not None:
        return _STATE_CACHE
    state = default_state()
    path = state_path()
    try:
        if os.path.exists(path):
            with open(path, "r") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                state.update(loaded)
                state["config"] = dict(default_state()["config"], **(loaded.get("config") or {}))
    except Exception as e:
        pass
    _STATE_CACHE = state
    return _STATE_CACHE

def save_state(state=None):
    global _STATE_CACHE
    _STATE_CACHE = state or load_state()
    path = state_path()
    tmp = path + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(_STATE_CACHE, f)
            f.flush()
            os.fsync(f.fileno())
        os.rename(tmp, path)
    except Exception as e:
        try:
            os.remove(tmp)
        except Exception:
            pass

def get_config(key, default=""):
    value = (load_state().get("config") or {}).get(key, default)
    if key == "tmdb_api_key" and not value:
        return DEFAULT_TMDB_KEY
    if key == "owner" and not value:
        return DEFAULT_OWNER
    return value

def set_config(key, value):
    state = load_state()
    state.setdefault("config", {})[key] = value
    save_state(state)