# -*- coding: utf-8 -*-
import os
import hashlib
import urllib.request as urllib2
from ...utils.base import fetch, SAFE_UA

POSTER_CACHE = "/tmp/multimovie_posters"

def poster_cache_path(url):
    if not url: return None
    if not os.path.exists(POSTER_CACHE):
        os.makedirs(POSTER_CACHE)
    h = hashlib.md5(url.encode('utf-8')).hexdigest()
    return os.path.join(POSTER_CACHE, f"{h}.jpg")

def get_cached_poster(url):
    path = poster_cache_path(url)
    if path and os.path.exists(path):
        return path
    return None

def download_poster(url):
    if not url: return None
    cached = get_cached_poster(url)
    if cached:
        return cached
    # fetch using base.py's fetch (which handles referer)
    # but we need the raw bytes – we can use base.py's fetch but it returns text
    # so we'll do a direct request with proper headers
    from urllib.request import Request, urlopen
    req = Request(url, headers={"User-Agent": SAFE_UA, "Referer": url})
    try:
        data = urlopen(req, timeout=10).read()
    except:
        return None
    path = poster_cache_path(url)
    if path:
        with open(path, "wb") as f:
            f.write(data)
        return path
    return None