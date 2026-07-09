# -*- coding: utf-8 -*-
"""
Poster caching and downloading utilities.
Handles WebP → JPEG conversion for Enigma2.
"""
import os
import re
import time
import hashlib
import threading
import urllib.request as urllib2
from .base import log
from .ui import SAFE_UA

_POSTER_CACHE_DIR = "/tmp/multimovie_posters"

def poster_cache_path(url):
    if not url:
        return None
    try:
        if not os.path.isdir(_POSTER_CACHE_DIR):
            os.makedirs(_POSTER_CACHE_DIR)
    except Exception:
        pass
    url_hash = hashlib.md5(url.encode("utf-8", "ignore")).hexdigest()
    return os.path.join(_POSTER_CACHE_DIR, "{}.jpg".format(url_hash))

def is_poster_cached(url):
    path = poster_cache_path(url)
    return path and os.path.exists(path)

def get_cached_poster(url):
    path = poster_cache_path(url)
    if path and os.path.exists(path):
        return path
    return None

def fetch_poster_bytes(url, referer, timeout=7):
    """
    Fetch poster image bytes, converting WebP to JPEG for Enigma2.
    """
    req = urllib2.Request(url, headers={"User-Agent": SAFE_UA, "Referer": referer})
    data = urllib2.urlopen(req, timeout=timeout).read()

    looks_like_webp = url.lower().split("?", 1)[0].endswith(".webp") or data[:4] == b"RIFF"
    if not looks_like_webp:
        return data

    # Attempt 1: convert via PIL if available
    try:
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(data)).convert("RGB")
        out = io.BytesIO()
        img.save(out, format="JPEG")
        return out.getvalue()
    except Exception:
        pass

    # Attempt 2: try same-named .jpg variant
    try:
        alt_url = re.sub(r'\.webp(\?.*)?$', lambda m: ".jpg" + (m.group(1) or ""), url, flags=re.I)
        if alt_url != url:
            alt_req = urllib2.Request(alt_url, headers={"User-Agent": SAFE_UA, "Referer": referer})
            alt_data = urllib2.urlopen(alt_req, timeout=timeout).read()
            if alt_data:
                return alt_data
    except Exception:
        pass

    return data  # fallback to original bytes

def download_poster(url, referer=None):
    """
    Download poster to cache and return local path, or None on failure.
    """
    if not url:
        return None
    if url.startswith("//"):
        url = "https:" + url

    cached = get_cached_poster(url)
    if cached:
        return cached

    # Derive referer from URL if not provided
    if not referer:
        from urllib.parse import urlparse
        p = urlparse(url)
        referer = "{}://{}/".format(p.scheme, p.netloc)

    try:
        data = fetch_poster_bytes(url, referer, timeout=10)
        cache_path = poster_cache_path(url)
        if cache_path:
            with open(cache_path, "wb") as f:
                f.write(data)
            return cache_path
        # fallback: save to temp
        tmp_path = "/tmp/multimovie_poster_{}.jpg".format(int(time.time()))
        with open(tmp_path, "wb") as f:
            f.write(data)
        return tmp_path
    except Exception as e:
        log("download_poster error: {}".format(e))
        return None