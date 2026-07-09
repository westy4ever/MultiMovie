# -*- coding: utf-8 -*-
"""
Player utilities: proxy server, candidate builder, play function.
"""
import os
import sys
import re
import time
import threading
import http.server
import urllib.request as urllib2
from urllib.parse import urlparse, parse_qs, urlencode, unquote
from enigma import eServiceReference
from .base import log
from .ui import SAFE_UA

_PROXY_PORT = 19888
_PROXY_STARTED = False
_PROXY_LAST_HIT = 0
_PROXY_LAST_BYTES = 0

# ─── Proxy Server ─────────────────────────────────────────────────────────────

class LocalProxyHandler(http.server.BaseHTTPRequestHandler):
    def do_HEAD(self):
        self._handle("HEAD")
    def do_GET(self):
        self._handle("GET")
    def _handle(self, method):
        try:
            global _PROXY_LAST_HIT, _PROXY_LAST_BYTES
            raw = self.path[1:]
            parsed_req = urlparse(self.path)
            query = parse_qs(parsed_req.query or "")
            piped_headers = ""
            if parsed_req.path == "/stream" and query.get("url"):
                stream_url = unquote(query.get("url", [""])[0]).strip()
                explicit_referer = unquote(query.get("referer", [""])[0]).strip()
                explicit_ua = unquote(query.get("ua", [""])[0]).strip()
            else:
                explicit_referer = ""
                explicit_ua = ""
                if not raw or "://" not in raw:
                    self.send_error(400, "Bad URL")
                    return
                if "|" in raw:
                    stream_url, piped_headers = raw.split("|", 1)
                    stream_url = stream_url.strip()
                else:
                    stream_url = raw.strip()
            headers = {"User-Agent": SAFE_UA}
            if explicit_ua:
                headers["User-Agent"] = explicit_ua
            if piped_headers:
                for part in piped_headers.split("&"):
                    if "=" in part:
                        k, v = part.split("=", 1)
                        headers[k.strip()] = v.strip()
            if explicit_referer:
                headers["Referer"] = explicit_referer
            elif "Referer" not in headers:
                try:
                    parts = stream_url.split("/")
                    headers["Referer"] = parts[0] + "//" + parts[2] + "/"
                except Exception:
                    pass
            range_hdr = self.headers.get("Range") or self.headers.get("range")
            if range_hdr:
                headers["Range"] = range_hdr
            log("Proxy: {} {}".format(method, stream_url[:80]))
            _PROXY_LAST_HIT = time.time()
            _PROXY_LAST_BYTES = 0
            req = urllib2.Request(stream_url, headers=headers)
            try:
                resp = urllib2.urlopen(req, timeout=30)
                status = resp.getcode()
            except urllib2.HTTPError as http_err:
                status = http_err.code
                resp = http_err
            except Exception as e:
                self.send_error(502, str(e))
                return
            self.send_response(status)
            resp_hdrs = {}
            try:
                for k, v in resp.getheaders():
                    resp_hdrs[k.lower()] = v
            except Exception:
                pass
            for key in ("content-type", "content-length", "content-range", "accept-ranges", "last-modified", "etag"):
                if key in resp_hdrs:
                    self.send_header(key.title(), resp_hdrs[key])
            if "accept-ranges" not in resp_hdrs:
                self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            if method == "HEAD":
                return
            try:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    _PROXY_LAST_BYTES += len(chunk)
                    self.wfile.write(chunk)
                    self.wfile.flush()
            except Exception:
                pass
        except Exception as e:
            log("Proxy FATAL: {}".format(e))
            try:
                self.send_error(500)
            except Exception:
                pass
    def log_message(self, *args):
        pass

def start_proxy():
    global _PROXY_STARTED
    if _PROXY_STARTED:
        return
    try:
        def run_server():
            server = http.server.HTTPServer(('0.0.0.0', _PROXY_PORT), LocalProxyHandler)
            server.serve_forever()
        t = threading.Thread(target=run_server)
        t.daemon = True
        t.start()
        _PROXY_STARTED = True
        log("LocalProxy Shield: ACTIVE (Port {})".format(_PROXY_PORT))
    except Exception as e:
        log("start_proxy failure: {}".format(e))

# ─── Candidate Builder ──────────────────────────────────────────────────────

def build_remote_play_candidates(url):
    url = str(url).strip()
    plain_url = url.split("#", 1)[0].strip()
    headers = {}
    if "#" in url:
        for part in url.split("#", 1)[1].split("&"):
            if "=" in part:
                key, value = part.split("=", 1)
                headers[key] = value
    candidates = []
    seen = set()
    def add_candidate(p_type, svc_url, label, uses_proxy=False):
        key = (p_type, svc_url)
        if not svc_url or key in seen:
            return
        seen.add(key)
        candidates.append((p_type, svc_url, label, uses_proxy))
    if plain_url.startswith("https://") or plain_url.startswith("http://"):
        proxy_params = {"url": plain_url}
        if headers.get("Referer"):
            proxy_params["referer"] = headers["Referer"]
        if headers.get("User-Agent"):
            proxy_params["ua"] = headers["User-Agent"]
        proxied = "http://127.0.0.1:{}/stream?{}".format(_PROXY_PORT, urlencode(proxy_params))
        start_proxy()
        legacy_raw = url.replace("#", "|") if "#" in url else url
        legacy_proxied = "http://127.0.0.1:{}/{}".format(_PROXY_PORT, legacy_raw)
    else:
        proxied = ""
        legacy_proxied = ""
    is_hls = any(x in plain_url.lower() for x in (".m3u8", "master.txt", "/hls", "/playlist"))
    if is_hls:
        add_candidate(4097, plain_url, "4097 مباشر HLS")
        if proxied:
            add_candidate(4097, proxied, "4097 + proxy HLS", True)
        add_candidate(4097, url, "4097 + headers HLS")
        add_candidate(8193, plain_url, "8193 مباشر")
        if proxied:
            add_candidate(8193, proxied, "8193 + proxy", True)
    else:
        if proxied:
            add_candidate(5001, proxied, "5001 + proxy", True)
        add_candidate(5001, plain_url, "5001 مباشر")
        add_candidate(8193, plain_url, "8193 مباشر")
        if proxied:
            add_candidate(8193, proxied, "8193 + proxy", True)
        add_candidate(4097, plain_url, "4097 مباشر")
        if proxied:
            add_candidate(4097, proxied, "4097 + proxy", True)
        add_candidate(4097, url, "4097 + headers")
    if legacy_proxied:
        add_candidate(4097, legacy_proxied, "4097 + proxy قديم", True)
    if os.path.exists("/usr/bin/exteplayer3"):
        if plain_url.startswith("http://") or plain_url.startswith("https://"):
            add_candidate(5002, plain_url, "5002 مباشر")
            if proxied:
                add_candidate(5002, proxied, "5002 + proxy", True)
        add_candidate(5002, url, "5002 + headers")
    return candidates

# ─── Player Helper Functions ────────────────────────────────────────────────

def _copy_service_ref(sref):
    if not sref:
        return None
    try:
        return eServiceReference(sref.toString())
    except Exception:
        try:
            return eServiceReference(str(sref.toString()))
        except Exception:
            return sref

def _capture_previous_service(session):
    try:
        return _copy_service_ref(session.nav.getCurrentlyPlayingServiceReference())
    except Exception as e:
        log("Capture previous service failed: {}".format(e))
        return None

def _restore_previous_service(session, previous_service):
    if not previous_service:
        return
    try:
        session.nav.stopService()
    except Exception:
        pass
    try:
        session.nav.playService(previous_service)
        log("Previous service restored")
    except Exception as e:
        log("Restore previous service failed: {}".format(e))

def play(session, url, title, resume_pos=0, item_url=""):
    """Global play function – decides between custom player and MoviePlayer."""
    from enigma import eServiceReference
    from Screens.InfoBar import MoviePlayer
    from ..screens.Player import MultiMoviePlayer
    svc_url = str(url).strip()
    is_remote = svc_url.startswith("http://") or svc_url.startswith("https://")
    previous_service = _capture_previous_service(session)
    if is_remote:
        candidates = build_remote_play_candidates(svc_url)
        session.open(MultiMoviePlayer, title, candidates, previous_service, resume_pos=resume_pos, item_url=item_url)
        return
    sref = eServiceReference(4097, 0, svc_url)
    if sys.version_info[0] == 3:
        sref.setName(str(title))
    else:
        sref.setName(title.encode("utf-8", "ignore"))
    callback = lambda *args: _restore_previous_service(session, previous_service)
    try:
        session.openWithCallback(callback, MoviePlayer, sref)
    except Exception as e:
        log("MoviePlayer fallback error: {}".format(e))