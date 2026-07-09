# -*- coding: utf-8 -*-
"""
Base extractor — common utilities + video host resolvers
Improvements over previous version:
  - Fixed egydead referer (tv8.egydead.live instead of stale x7k9f.sbs)
  - fetch() retry on transient failures (503 / timeout)
  - New resolvers: ok.ru, filemoon, streamwish family, lulustream, vidguard
  - Improved: streamtape (3 fallback patterns), doodstream (15+ domains),
               voe (base64 + newer layouts), resolve_iframe_chain (meta-refresh,
               JS location, data-src)
  - _best_media_url: richer source patterns (jwplayer, sources[], clappr)
  - Unicode URL support for Arabic characters
  - Added fastvid.cam resolver
  - Added rpmvip/upshare/cleantechworld resolvers
  - NEW: Added faselhd specific resolvers for scdns.io and datahowa.asia
  - NEW: Added downet.net resolver for Akwam direct MP4s
  - NEW: Added govid.live resolver for faselhd.rip
  - NEW: Added referer support for faselhd.rip and datahowa.asia
  - LOG: Now writes to /tmp/multimovie.log
"""

import re
import json
import time
import random
import base64
from urllib.request import Request, urlopen, build_opener, HTTPCookieProcessor, HTTPSHandler
from urllib.parse import urljoin, urlparse, unquote, urlencode, quote_plus
from urllib.error import URLError, HTTPError
import http.cookiejar as cookiejar
import ssl
import gzip
import zlib
import io
import sys

# brotli imports (try both common packages)
try:
    import brotli
except ImportError:
    try:
        import brotlicffi as brotli
    except ImportError:
        brotli = None

UA      = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
TIMEOUT = 30
ACCEPT_ENCODING = "gzip, deflate, br" if brotli is not None else "gzip, deflate"

_opener = None
_cookiejar = None


# ─── Logging ──────────────────────────────────────────────────────────────────
def log(msg):
    """Write timestamped message to /tmp/multimovie.log"""
    try:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        line = "[{}] {}\n".format(ts, msg)
        with open("/tmp/multimovie.log", "a") as f:
            f.write(line)
        print("[MultiMovie] {}".format(msg))
    except Exception:
        pass


def _get_opener():
    global _opener, _cookiejar
    if _opener:
        return _opener
    _cookiejar = cookiejar.CookieJar()
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    except AttributeError:
        ctx = ssl._create_unverified_context()
    _opener = build_opener(HTTPCookieProcessor(_cookiejar), HTTPSHandler(context=ctx))
    return _opener


def clear_cookies(domain=None):
    """Clear cookies for this addon's shared session, optionally scoped to a domain substring."""
    global _cookiejar
    _get_opener()
    if _cookiejar is None:
        return
    try:
        if domain:
            for cookie in list(_cookiejar):
                if domain in (cookie.domain or ""):
                    _cookiejar.clear(cookie.domain, cookie.path, cookie.name)
        else:
            _cookiejar.clear()
    except Exception as e:
        log("clear_cookies error: {}".format(e))


def _decode_response_body(raw, info):
    """
    Decode response body handling various encodings and compression.
    """
    ce = info.get("Content-Encoding", "").lower()
    
    # Handle Content-Encoding compression
    if "gzip" in ce:
        try:
            raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
        except Exception as e:
            log("Gzip decompression error: {}".format(e))
            
    elif "deflate" in ce:
        try:
            # Try with -zlib.MAX_WBITS for raw deflate
            raw = zlib.decompress(raw, -zlib.MAX_WBITS)
        except Exception:
            try:
                # Try standard zlib decompression
                raw = zlib.decompress(raw)
            except Exception as e:
                log("Deflate decompression error: {}".format(e))
                
    elif "br" in ce and brotli is not None:
        try:
            raw = brotli.decompress(raw)
        except Exception as e:
            log("Brotli decompression error: {}".format(e))
    
    # Detect charset from Content-Type header
    charset = "utf-8"
    ctype = info.get("Content-Type", "").lower()
    
    # Extract charset from Content-Type (e.g., "text/html; charset=utf-8")
    charset_match = re.search(r'charset=([^\s;]+)', ctype, re.I)
    if charset_match:
        detected_charset = charset_match.group(1).strip()
        # Remove quotes if present
        detected_charset = detected_charset.strip('"\'')
        if detected_charset:
            charset = detected_charset
    
    # List of decoders to try in order
    decoders = []
    if charset and charset not in ["utf-8", "utf8"]:
        decoders.append((charset, "Detected charset"))
    decoders.extend([
        ("utf-8", "UTF-8 fallback"),
        ("windows-1256", "Arabic Windows fallback"),
        ("iso-8859-6", "Arabic ISO fallback"),
        ("latin-1", "Latin-1 fallback (preserves bytes)"),
    ])
    
    for enc, name in decoders:
        try:
            result = raw.decode(enc)
            if enc != "utf-8" and enc != charset:
                log("Decoded with {} encoding".format(name))
            return result
        except (UnicodeDecodeError, LookupError):
            continue
    
    # Last resort: replace errors
    try:
        return raw.decode("utf-8", errors="replace")
    except Exception:
        return raw.decode("latin-1", errors="replace")


def _encode_unicode_url(url):
    """Encode Unicode characters in URL to percent-encoded format."""
    try:
        parsed = urlparse(url)
        # Encode the path if it contains non-ASCII
        path_segments = []
        for segment in parsed.path.split('/'):
            if segment:
                # Check if segment contains non-ASCII
                if any(ord(c) > 127 for c in segment):
                    path_segments.append(quote_plus(segment.encode('utf-8')))
                else:
                    path_segments.append(segment)
            else:
                path_segments.append('')
        encoded_path = '/'.join(path_segments)
        if not encoded_path.startswith('/'):
            encoded_path = '/' + encoded_path
        
        # Also encode query parameters if needed
        encoded_query = ''
        if parsed.query:
            try:
                # Parse query string and encode values
                query_parts = []
                for part in parsed.query.split('&'):
                    if '=' in part:
                        key, val = part.split('=', 1)
                        if any(ord(c) > 127 for c in val):
                            query_parts.append(key + '=' + quote_plus(val.encode('utf-8')))
                        else:
                            query_parts.append(part)
                    else:
                        query_parts.append(part)
                encoded_query = '&'.join(query_parts)
            except Exception:
                encoded_query = parsed.query
        
        # Rebuild URL
        from urllib.parse import urlunparse
        encoded_url = urlunparse((
            parsed.scheme,
            parsed.netloc,
            encoded_path,
            parsed.params,
            encoded_query,
            parsed.fragment
        ))
        return encoded_url
    except Exception:
        return url


def fetch(url, referer=None, extra_headers=None, post_data=None):
    """
    Robust fetch with:
    - Smart per-domain referer defaults
    - Auto retry on transient errors (503, timeout, connection reset)
    - Brotli / gzip / deflate decompression
    - Cookie jar (shared session)
    - Unicode URL support (properly encodes Arabic/etc. characters)
    """
    max_retries = 2
    for attempt in range(max_retries + 1):
        try:
            opener = _get_opener()
            
            # Handle Unicode URLs - encode to percent-encoded format
            encoded_url = _encode_unicode_url(url)
            
            parsed = urlparse(encoded_url)
            domain = parsed.netloc.lower()

            if not referer:
                # ── per-domain referer defaults ───────────────────────────
                # SPECIFIC FaselHD domains FIRST (before generic catch)
                if "faselhd.rip" in domain:
                    referer = "https://faselhd.rip/"
                elif "web596x.faselhdx.bid" in domain or "web5106x.faselhdx.bid" in domain:
                    referer = "https://web5106x.faselhdx.bid/"
                elif "govid.live" in domain:
                    referer = "https://faselhd.rip/"
                elif "datahowa.asia" in domain:
                    referer = "https://faselhd.rip/"
                elif "scdns.io" in domain:
                    referer = "https://web5106x.faselhdx.bid/"
                # Generic Fasel catch (only for other fasel domains)
                elif "fasel" in domain or "faselhdx" in domain or "fasel-hd" in domain:
                    referer = "https://www.fasel-hd.cam/"
                # Other sites
                elif "egydead" in domain:
                    referer = "{}://{}/".format(parsed.scheme, domain)
                elif "wecima" in domain or "mycima" in domain:
                    referer = "https://wecima.click/"
                elif "downet.net" in domain:
                    referer = "https://akwam.com.co/"
                elif "topcinema" in domain:
                    referer = "https://topcinemaa.com/"
                elif "shaheed" in domain or "shahid" in domain:
                    referer = "https://shahidd4u.com/"
                elif "streamwish" in domain or "wishfast" in domain:
                    referer = "https://streamwish.to/"
                elif "filemoon" in domain:
                    referer = "https://filemoon.sx/"
                elif "lulustream" in domain:
                    referer = "https://lulustream.com/"
                elif "ok.ru" in domain:
                    referer = "https://ok.ru/"
                elif "vidguard" in domain or "vgfplay" in domain:
                    referer = "https://vidguard.to/"
                elif "filelion" in domain or "vidhide" in domain or "streamhide" in domain:
                    referer = "https://filelions.to/"
                elif "fastvid" in domain:
                    referer = "https://fastvid.cam/"
                elif "rpmvip" in domain:
                    referer = "https://shaaheid4u.rpmvip.com/"
                elif "upn.one" in domain or "upshare" in domain:
                    referer = "https://shiid4u.upn.one/"
                # ========  ADD THESE FOUR LINES BELOW ========
                elif "savefiles.com" in domain or "mxcontent.net" in domain or "delucloud.xyz" in domain or "sprintcdn.com" in domain:
                    referer = "https://wecima.cx/"
                elif "tnmr.org" in domain or "aurorafieldnetwork.store" in domain:
                    referer = "https://wecima.cx/"
                # =============================================    
                else:
                    referer = "{}://{}/".format(parsed.scheme, domain)

            headers = {
                "User-Agent": UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                "Accept-Language": "ar,en-US,en;q=0.9",
                "Accept-Encoding": ACCEPT_ENCODING,
                "Connection": "keep-alive",
                "Referer": referer,
                "Upgrade-Insecure-Requests": "1",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
            }
            # FIX: "get__quality__servers/" (arabseed's first AJAX call) only
            # matched none of the previous substrings ("get__watch" matched
            # the *second* call, get__watch__server/, but not this one) -
            # so it was sent with normal document-navigation headers instead
            # of AJAX headers, causing the server to reject it with a fixed
            # tiny error payload (consistently 49 bytes in logs) instead of
            # real per-quality server data.
            if any(x in encoded_url.lower() for x in ["ajax", "get__watch", "get__quality", "api/", ".json"]):
                headers.update({
                    "X-Requested-With": "XMLHttpRequest",
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                    # FIX: different episodes were getting back identical
                    # responses for these endpoints - matches an
                    # intermediate cache keying on URL alone and ignoring
                    # the POST body (post_id). Explicitly forbid caching,
                    # on top of the per-request cache-busting query param
                    # arabseed.py now adds to these same URLs.
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache",
                    "Sec-Fetch-Dest": "empty",
                    "Sec-Fetch-Mode": "cors",
                })
            if extra_headers:
                headers.update(extra_headers)

            data = post_data
            if data and isinstance(data, dict):
                data = urlencode(data).encode("utf-8")
                headers["Content-Type"] = "application/x-www-form-urlencoded"
            elif data and isinstance(data, (str, bytes)):
                if isinstance(data, str):
                    data = data.encode("utf-8")

            log("Fetching (attempt {}): {}".format(attempt + 1, encoded_url))
            req = Request(encoded_url, headers=headers, data=data)

            with opener.open(req, timeout=TIMEOUT) as resp:
                raw = resp.read()
                final_url = resp.geturl()
                info = resp.info()

                if any(x in final_url.lower() for x in ("alliance4creativity.com", "watch-it-legally")):
                    log("!!! ACE Redirect detected for {} !!!".format(encoded_url))
                    return None, final_url

                html = _decode_response_body(raw, info)
                log("Fetch OK: {} ({} bytes)".format(final_url, len(html)))
                return html, final_url

        except HTTPError as e:
            # Retry on 503 / 429 / 502 / 504
            if attempt < max_retries and e.code in (503, 429, 502, 504):
                log("Fetch HTTPError {}, retrying in 2s: {}".format(e.code, url))
                time.sleep(2)
                continue
            try:
                raw = e.read()
                html = _decode_response_body(raw, e.info()) if raw else ""
                log("Fetch HTTPError: {} → {} {} ({} bytes)".format(url, e.code, e.reason, len(html)))
            except Exception:
                log("Fetch HTTPError: {} → {} {}".format(url, getattr(e, "code", "?"), getattr(e, "reason", e)))
            return None, url

        except URLError as e:
            if attempt < max_retries:
                log("Fetch URLError (retry {}): {} → {}".format(attempt + 1, url, e))
                global _opener
                _opener = None           # reset opener on network error
                time.sleep(1.5)
                continue
            log("Fetch URLError: {} → {}".format(url, e))
            _opener = None
            return None, url

        except UnicodeEncodeError as e:
            # Handle Unicode encoding errors specifically
            log("Fetch UnicodeEncodeError: {} → {}".format(url, e))
            # Try with manual encoding
            try:
                # Fallback: try to encode the URL explicitly
                encoded_url = url.encode('utf-8').decode('ascii', errors='ignore')
                if encoded_url != url:
                    log("Retrying with encoded URL: {}".format(encoded_url))
                    return fetch(encoded_url, referer, extra_headers, post_data)
            except Exception:
                pass
            return None, url

        except Exception as e:
            if attempt < max_retries:
                log("Fetch Error (retry {}): {} → {}".format(attempt + 1, url, e))
                time.sleep(1)
                continue
            log("Fetch Error: {} → {}".format(url, e))
            return None, url

    return None, url


def fetch_json(url, referer=None, extra_headers=None, post_data=None):
    """
    Fetch a URL and parse the response body as JSON.

    Every extractor generated by build_extractor.py imports this
    (`from .base import log, fetch, resolve_iframe_chain, fetch_json`)
    to hit detected API endpoints. Returns a dict/list on success, or
    None on failure (network error or invalid JSON) — callers use
    `data = fetch_json(...)` then `data.get(...)`, mirroring fetch()'s
    "return None on failure" convention.
    """
    html, _ = fetch(url, referer=referer, extra_headers=extra_headers, post_data=post_data)
    if not html:
        return None
    try:
        return json.loads(html)
    except (ValueError, TypeError) as e:
        log("fetch_json: failed to parse JSON from {}: {}".format(url, e))
        return None


# ─── HTML helpers ─────────────────────────────────────────────────────────────

def extract_iframes(html, base_url=""):
    iframes = re.findall(r'<iframe[^>]+src=["\']([^"\']+)["\']', html, re.I)
    result = []
    for src in iframes:
        if src.startswith("//"):
            src = "https:" + src
        elif src.startswith("/") and base_url:
            p = urlparse(base_url)
            src = "{}://{}{}".format(p.scheme, p.netloc, src)
        if src.startswith("http"):
            result.append(src)
    return result


def find_m3u8(html):
    if not html:
        return None
    patterns = [
        r'["\']([^"\']+\.m3u8[^"\']*)["\']',
        r'file\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
        r'source\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
        r'hls\.loadSource\(["\']([^"\']+)["\']',
        r'"url"\s*:\s*"([^"]+\.m3u8[^"]*)"',
        r'data-(?:url|src)=["\']([^"\']+\.m3u8[^"\']*)["\']',
        r'hlsManifestUrl["\']?\s*:\s*["\']([^"\']+)["\']',
    ]
    for p in patterns:
        m = re.search(p, html, re.I)
        if m:
            url = m.group(1).replace("\\/", "/").replace("&amp;", "&").replace("\\u0026", "&").strip()
            if url.startswith("//"):
                url = "https:" + url
            if url.startswith("http") and ".m3u8" in url:
                return url
    return None


def find_mp4(html):
    if not html:
        return None
    patterns = [
        r'["\']([^"\']+\.mp4[^"\']*)["\']',
        r'file\s*:\s*["\']([^"\']+\.mp4[^"\']*)["\']',
        r'data-(?:url|src)=["\']([^"\']+\.mp4[^"\']*)["\']',
    ]
    for p in patterns:
        m = re.search(p, html, re.I)
        if m:
            url = m.group(1).replace("\\/", "/").replace("&amp;", "&").strip()
            if url.startswith("//"):
                url = "https:" + url
            if url.startswith("http") and ".mp4" in url:
                return url
    return None


def _best_media_url(text):
    """
    Pick the highest-quality video URL visible in plain or unpacked JS.
    Covers: direct URLs, JWPlayer setup, sources[], Clappr, HLS manifests.
    """
    if not text:
        return None
    candidates = []
    seen = set()

    def score(url):
        lowered = url.lower()
        if "2160" in lowered or "4k" in lowered:   return 5000
        if "1080" in lowered or "fhd" in lowered:  return 4000
        if "720" in lowered  or "hd" in lowered:   return 3000
        if "480" in lowered:                        return 2000
        if "360" in lowered:                        return 1000
        if "240" in lowered or "sd" in lowered:     return 500
        if ".m3u8" in lowered:                      return 3500
        return 100

    patterns = [
        # JWPlayer / sources array
        r'sources\s*:\s*\[{[^}]*file\s*:\s*["\']([^"\']+)["\']',
        r'"file"\s*:\s*"([^"]+(?:m3u8|mp4)[^"]*)"',
        r"'file'\s*:\s*'([^']+(?:m3u8|mp4)[^']*)'",
        # Clappr / hls.js
        r'"source"\s*:\s*"([^"]+(?:m3u8|mp4)[^"]*)"',
        r"'source'\s*:\s*'([^']+(?:m3u8|mp4)[^']*)'",
        r'"src"\s*:\s*"([^"]+(?:m3u8|mp4)[^"]*)"',
        # Direct URLs
        r'(https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*)',
        r'(https?://[^\s"\'<>]+\.mp4[^\s"\'<>]*)',
        # hlsManifestUrl (ok.ru, etc.)
        r'hlsManifestUrl["\']?\s*:\s*["\']([^"\']+)["\']',
        # playlist / stream
        r'"(?:playlist|stream|hls|hls2|master)"\s*:\s*"([^"]+)"',
        r"'(?:playlist|stream|hls|hls2|master)'\s*:\s*'([^']+)'",
    ]
    for pat in patterns:
        for match in re.findall(pat, text, re.I):
            url = match.replace("\\/", "/").replace("&amp;", "&").replace("\\u0026", "&").strip()
            if url.startswith("//"):
                url = "https:" + url
            if not url.startswith("http"):
                continue
            if url in seen:
                continue
            seen.add(url)
            candidates.append((score(url), url))

    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


# ─── Packer / obfuscation ─────────────────────────────────────────────────────

def _extract_packer_blocks(html):
    blocks = []
    marker = "eval(function(p,a,c,k,e,d){"
    tail   = ".split('|')))"
    pos = 0
    while True:
        start = (html or "").find(marker, pos)
        if start == -1:
            break
        end = (html or "").find(tail, start)
        if end == -1:
            break
        blocks.append(html[start : end + len(tail)])
        pos = end + len(tail)
    return blocks


def decode_packer(packed):
    try:
        def read_js_string(text, start_idx):
            quote = text[start_idx]
            i = start_idx + 1
            out = []
            while i < len(text):
                ch = text[i]
                if ch == "\\" and i + 1 < len(text):
                    out.append(text[i + 1])
                    i += 2
                    continue
                if ch == quote:
                    return "".join(out), i + 1
                out.append(ch)
                i += 1
            return "", -1

        start = packed.find("}(")
        if start == -1:
            return ""
        idx = start + 2
        while idx < len(packed) and packed[idx] in " \t\r\n":
            idx += 1
        if idx >= len(packed) or packed[idx] not in ("'", '"'):
            return ""

        p, idx = read_js_string(packed, idx)
        if idx == -1:
            return ""

        nums = re.match(r"\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*", packed[idx:], re.S)
        if not nums:
            return ""
        a, c = nums.group(1), nums.group(2)
        idx += nums.end()
        if idx >= len(packed) or packed[idx] not in ("'", '"'):
            return ""

        k, idx = read_js_string(packed, idx)
        if idx == -1:
            return ""

        a, c = int(a), int(c)
        k = k.split("|")

        def e(c_val):
            result = ""
            while True:
                result = "0123456789abcdefghijklmnopqrstuvwxyz"[c_val % a] + result
                c_val //= a
                if c_val == 0:
                    break
            return result

        d = {e(i): k[i] or e(i) for i in range(c)}
        return re.sub(r'\b(\w+)\b', lambda x: d.get(x.group(1), x.group(1)), p)
    except Exception:
        return ""


def find_packed_links(html):
    for ev in _extract_packer_blocks(html):
        dec = decode_packer(ev)
        if dec:
            res = find_m3u8(dec) or find_mp4(dec)
            if res:
                return res
    # fallback broader eval pattern
    for ev in re.findall(r"eval\(function\(p,a,c,k,e,d\).*?}\(.*?\)\)", html, re.S):
        dec = decode_packer(ev)
        if dec:
            res = find_m3u8(dec) or find_mp4(dec)
            if res:
                return res
    return None


def _unpack_all(html):
    """Return list of (original_html + all unpacked JS blocks) for thorough scanning."""
    texts = [html]
    for block in _extract_packer_blocks(html):
        dec = decode_packer(block)
        if dec:
            texts.append(dec)
    return texts


# ─── Video Host Resolvers ─────────────────────────────────────────────────────

def resolve_streamtape(url):
    """streamtape.com — tries 3 extraction patterns as the site changes often."""
    try:
        html, _ = fetch(url, referer="https://streamtape.com/")
        if not html:
            return None

        # Pattern 1: robotlink innerHTML concat (classic)
        m = re.search(r"robotlink\)\.innerHTML\s*=\s*'([^']+)'\s*\+\s*'([^']+)'", html)
        if m:
            link = m.group(1) + m.group(2)
            if not link.startswith("http"):
                link = "https:" + link
            return link.replace("//streamtape.com", "https://streamtape.com")

        # Pattern 2: single innerHTML assignment
        m = re.search(r"robotlink\)\.innerHTML\s*=\s*['\"]([^'\"]+)['\"]", html)
        if m:
            link = m.group(1)
            return ("https:" + link) if link.startswith("//") else link

        # Pattern 3: /get_video?... inside JS
        m = re.search(r'(/get_video\?[^"\'&\s]+)', html)
        if m:
            return "https://streamtape.com" + m.group(1)

        # Pattern 4: direct mp4 URL
        return find_mp4(html)
    except Exception:
        pass
    return None


def resolve_doodstream(url):
    """dood.* / doodstream / dsv* / d0o0d and 20+ domain variants."""
    DOOD_DOMAINS = [
        "dood.re", "dood.to", "dood.so", "dood.pm", "dood.ws",
        "dood.watch", "dood.sh", "dood.la", "dood.li", "dood.cx",
        "dood.xyz", "dood.wf", "d0o0d.com", "dsvplay.com",
        "doods.pro", "ds2play.com", "dooood.com", "doodstream.com",
    ]
    try:
        # Normalise to a working domain
        working_html = None
        working_url  = url
        for dom in DOOD_DOMAINS:
            candidate = re.sub(r'dood\.[a-z]+|dsvplay\.[a-z]+|d0o0d\.[a-z]+|doodstream\.[a-z]+', dom, url)
            html, final = fetch(candidate, referer=candidate)
            if html and "pass_md5" in html:
                working_html = html
                working_url  = candidate
                break
        if not working_html:
            working_html, _ = fetch(url, referer=url)
        if not working_html:
            return None

        m = re.search(r'\$\.get\(["\'](/pass_md5/[^"\']+)["\']', working_html)
        if not m:
            m = re.search(r'pass_md5/([^"\'.\s&]+)', working_html)
            if m:
                pass_path = "/pass_md5/" + m.group(1)
            else:
                return None
        else:
            pass_path = m.group(1)

        # Extract base domain from working URL
        parsed = urlparse(working_url)
        dood_base = "{}://{}".format(parsed.scheme, parsed.netloc)

        token_html, _ = fetch(dood_base + pass_path, referer=working_url)
        if not token_html:
            return None

        chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
        rand = "".join(random.choice(chars) for _ in range(10))
        token = pass_path.split("/")[-1]
        return "{}{}&token={}&expiry={}".format(
            token_html.strip(), rand, token, int(time.time() * 1000)
        )
    except Exception:
        pass
    return None


def resolve_vidbom(url):
    try:
        html, _ = fetch(url, referer=url)
        if not html:
            return None
        return find_m3u8(html) or find_mp4(html) or find_packed_links(html)
    except Exception:
        pass
    return None


def resolve_uqload(url):
    try:
        html, _ = fetch(url, referer=url)
        if not html:
            return None
        m = re.search(r'sources:\s*\["([^"]+)"\]', html)
        if m:
            return m.group(1)
        return find_m3u8(html) or find_mp4(html)
    except Exception:
        pass
    return None


def resolve_govid(url):
    """govid.live - enhanced resolver for faselhd.rip streams"""
    try:
        # If it's already a m3u8, return it
        if '.m3u8' in url:
            log("resolve_govid: direct m3u8 URL")
            return url
        
        html, _ = fetch(url, referer="https://faselhd.rip/")
        if not html:
            return None
        
        # Look for m3u8 in the response
        m3u8 = find_m3u8(html)
        if m3u8:
            log("resolve_govid: found m3u8: {}".format(m3u8[:80]))
            return m3u8
        
        return find_mp4(html)
    except Exception:
        pass
    return None


def resolve_upstream(url):
    try:
        html, _ = fetch(url, referer=url)
        if not html:
            return None
        return find_m3u8(html) or find_mp4(html)
    except Exception:
        pass
    return None


def resolve_mixdrop(url):
    try:
        html, _ = fetch(url, referer=url)
        if not html:
            return None
        # Direct MDCore pattern
        m = re.search(r'MDCore\.wurl\s*=\s*"([^"]+)"', html)
        if m:
            link = m.group(1)
            return ("https:" + link) if link.startswith("//") else link
        # Packed JS
        for txt in _unpack_all(html):
            m = re.search(r'MDCore\.wurl\s*=\s*"([^"]+)"', txt)
            if m:
                link = m.group(1)
                return ("https:" + link) if link.startswith("//") else link
    except Exception:
        pass
    return None


def resolve_voe(url):
    """voe.sx — handles multiple obfuscation layers including base64 and newer layouts."""
    try:
        html, final = fetch(url, referer="https://voe.sx/")
        if not html:
            return None

        # Layer 1: direct hls / sources patterns
        for pat in [
            r"'hls'\s*:\s*'([^']+)'",
            r'"hls"\s*:\s*"([^"]+)"',
            r"sources\s*=\s*\[{[^}]*file\s*:\s*'([^']+)'",
            r'"file"\s*:\s*"([^"]+\.m3u8[^"]*)"',
        ]:
            m = re.search(pat, html, re.I)
            if m:
                return m.group(1).replace("\\/", "/")

        # Layer 2: base64 atob() blobs
        import base64
        for enc in re.finditer(r'atob\([\'"]([A-Za-z0-9+/=]+)[\'"]\)', html):
            try:
                dec = base64.b64decode(enc.group(1) + "==").decode("utf-8", errors="ignore")
                mm = re.search(r'(https?://[^\s\'"<>]+\.m3u8[^\s\'"<>]*)', dec)
                if mm:
                    return mm.group(1)
            except Exception:
                pass

        # Layer 3: packed JS
        for txt in _unpack_all(html):
            best = _best_media_url(txt)
            if best:
                return best

        direct = find_m3u8(html) or find_mp4(html)
        if direct:
            return direct

        # Layer 4: voe.sx sometimes wraps a completely different, randomly
        # named domain via a nested iframe instead of embedding the stream
        # on its own page (confirmed via a real captured session - the
        # same "wrapper embeds unrelated domain" pattern already seen with
        # hgcloud.to). If nothing was found directly above, follow any
        # iframe pointing elsewhere and try resolving that instead.
        for embed_url in re.findall(r'<iframe[^>]+src=["\']([^"\']+)["\']', html, re.I):
            if not embed_url.startswith('http') or 'voe.sx' in embed_url:
                continue
            log("voe: Found wrapped embed iframe: {}".format(embed_url))
            result = resolve_host(embed_url)
            if result:
                return result

        return None
    except Exception:
        pass
    return None


def resolve_streamruby(url):
    try:
        html, _ = fetch(url, referer=url)
        if not html:
            return None
        res = find_m3u8(html) or find_mp4(html)
        if res:
            return res
        for txt in _unpack_all(html):
            res = find_m3u8(txt) or find_mp4(txt)
            if res:
                return res
    except Exception:
        pass
    return None


def resolve_hgcloud(url):
    """hgcloud.to acts as a wrapper that embeds a backend video host inside
    a nested iframe. Which host it embeds is NOT fixed - masukestin.com and
    vibuxer.com have both been observed in practice (confirmed via a real
    captured session). The masukestin-specific checks are kept as-is since
    they're proven to work for that host's exact URL shape; the generic
    checks below are added as additional fallbacks for whatever other
    backend hgcloud.to happens to be using, instead of returning None for
    anything that isn't masukestin."""
    try:
        html, final_url = fetch(url, referer="https://hgcloud.to/")
        if not html:
            return None

        from .base import resolve_host

        # Check if there's an iframe pointing to masukestin.com
        iframe_match = re.search(r'<iframe[^>]+src=["\']([^"\']+masukestin\.com[^"\']+)["\']', html, re.I)
        if iframe_match:
            embed_url = iframe_match.group(1)
            log("hgcloud: Found masukestin embed: {}".format(embed_url))
            result = resolve_host(embed_url)
            if result:
                return result

        # Check for meta refresh redirect
        meta_refresh = re.search(r'<meta[^>]+http-equiv=["\']refresh["\'][^>]+content=["\']\d+;\s*url=([^"\']+)["\']', html, re.I)
        if meta_refresh:
            redirect_url = meta_refresh.group(1)
            if "masukestin" in redirect_url:
                log("hgcloud: Redirecting to masukestin: {}".format(redirect_url))
                result = resolve_host(redirect_url)
                if result:
                    return result

        # Check if the page has JavaScript that loads masukestin
        if "masukestin" in html:
            masukestin_urls = re.findall(r'(https?://masukestin\.com/[^\s"\']+)', html)
            for masukestin_url in masukestin_urls:
                log("hgcloud: Found masukestin URL: {}".format(masukestin_url))
                result = resolve_host(masukestin_url)
                if result:
                    return result

        # Fallback for any OTHER backend (e.g. vibuxer.com) that hgcloud.to
        # might be embedding instead of masukestin: any iframe pointing
        # somewhere other than hgcloud.to itself.
        for embed_url in re.findall(r'<iframe[^>]+src=["\']([^"\']+)["\']', html, re.I):
            if not embed_url.startswith('http') or 'hgcloud.to' in embed_url or 'masukestin.com' in embed_url:
                continue
            log("hgcloud: Found non-masukestin embed iframe: {}".format(embed_url))
            result = resolve_host(embed_url)
            if result:
                return result

        # Same idea, but for an embed URL referenced directly in inline JS
        # rather than sitting in an <iframe> tag - matches a generic
        # "<host>/e/<id>" or "<host>/v/<id>" embed-URL shape.
        for embed_url in re.findall(r'(https?://[a-z0-9.-]+\.[a-z]{2,}/[ev]/[a-zA-Z0-9]+)', html, re.I):
            if 'hgcloud.to' in embed_url or 'masukestin.com' in embed_url:
                continue
            log("hgcloud: Found non-masukestin embed URL in page script: {}".format(embed_url))
            result = resolve_host(embed_url)
            if result:
                return result

        return None
    except Exception as e:
        log("resolve_hgcloud error: {}".format(e))
        return None


def resolve_vidtube(url):
    """vidtube.one — JWPlayer behind packer, optional domain restriction bypass."""
    try:
        html, _ = fetch(url, referer="https://topcinema.fan/")
        if not html or "restricted for this domain" in html.lower():
            html, _ = fetch(url, referer="https://topcinema.fan/")
        if not html:
            return None
        best = _best_media_url(html)
        if best:
            return best
        for txt in _unpack_all(html):
            best = _best_media_url(txt)
            if best:
                return best
    except Exception:
        pass
    return None

def resolve_masukestin(url):
    """masukestin.com - extracts m3u8 video URL from embed page"""
    try:
        html, final_url = fetch(url, referer="https://masukestin.com/")
        if not html:
            return None

        # Look for the stream URL pattern in the page
        stream_patterns = [
            r'(https?://masukestin\.com/stream/[^\s"\']+\.m3u8[^\s"\']*)',
            r'(https?://masukestin\.com/stream/[^\s"\']+)',
            r'streamUrl\s*:\s*["\']([^"\']+)["\']',
            r'videoUrl\s*:\s*["\']([^"\']+)["\']',
            r'src:\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
            r'file:\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
        ]

        for pattern in stream_patterns:
            match = re.search(pattern, html, re.I)
            if match:
                stream_url = match.group(1)
                stream_url = stream_url.replace("\\/", "/").replace("&amp;", "&")
                if stream_url.startswith("//"):
                    stream_url = "https:" + stream_url
                if ".m3u8" in stream_url:
                    log("masukestin: Found m3u8 stream: {}".format(stream_url[:80]))
                    return stream_url

        # Look in script tags
        script_tags = re.findall(r'<script[^>]*>(.*?)</script>', html, re.S | re.I)
        for script in script_tags:
            for pattern in stream_patterns:
                match = re.search(pattern, script, re.I)
                if match:
                    stream_url = match.group(1)
                    if ".m3u8" in stream_url:
                        log("masukestin: Found m3u8 in script: {}".format(stream_url[:80]))
                        return stream_url

        # Look for base64 encoded URLs
        b64_patterns = [
            r'atob\(["\']([A-Za-z0-9+/=]+)["\']\)',
            r'Base64\.decode\(["\']([A-Za-z0-9+/=]+)["\']\)',
        ]
        for pattern in b64_patterns:
            for match in re.findall(pattern, html):
                try:
                    import base64
                    decoded = base64.b64decode(match).decode('utf-8')
                    stream_match = re.search(r'(https?://masukestin\.com/stream/[^\s"\']+\.m3u8[^\s"\']*)', decoded)
                    if stream_match:
                        log("masukestin: Found m3u8 in base64: {}".format(stream_match.group(1)[:80]))
                        return stream_match.group(1)
                except:
                    pass

        log("masukestin: No stream URL found")
        return None
    except Exception as e:
        log("resolve_masukestin error: {}".format(e))
        return None

# ── NEW resolvers ──────────────────────────────────────────────────────────────

def resolve_streamwish(url):
    """
    StreamWish / WishFast / Filelions / VidHide / StreamHide / DHTpre —
    all run the same JWPlayer-based platform.
    """
    try:
        html, _ = fetch(url, referer=url)
        if not html:
            return None

        # Try direct patterns first (fastest)
        best = _best_media_url(html)
        if best:
            return best

        # Packed JS (all these sites heavily pack their JS)
        for txt in _unpack_all(html):
            best = _best_media_url(txt)
            if best:
                return best

        return find_m3u8(html) or find_mp4(html)
    except Exception:
        pass
    return None


def resolve_filemoon(url):
    """
    Filemoon.sx / .to / .nl / .wf — packed JS containing JWPlayer setup.
    Uses parserBYSE in e2iplayer (= packed → JWPlayer sources).
    """
    try:
        html, _ = fetch(url, referer="https://filemoon.sx/")
        if not html:
            return None

        # Direct scan first
        best = _best_media_url(html)
        if best:
            return best

        # Unpack all eval blocks
        for txt in _unpack_all(html):
            best = _best_media_url(txt)
            if best:
                return best

        # base64 blobs
        import base64
        for b64 in re.findall(r'atob\(["\']([A-Za-z0-9+/=]{40,})["\']\)', html, re.I):
            try:
                dec = base64.b64decode(b64 + "==").decode("utf-8", "ignore")
                best = _best_media_url(dec)
                if best:
                    return best
            except Exception:
                pass

        return find_m3u8(html) or find_mp4(html)
    except Exception:
        pass
    return None


def resolve_lulustream(url):
    """
    LuluStream — JWPlayer based, similar to streamwish family.
    Requires Referer: https://1fo1ndyf09qz.tnmr.org (confirmed from e2iplayer).
    """
    try:
        html, _ = fetch(url, referer="https://1fo1ndyf09qz.tnmr.org",
                        extra_headers={"Origin": "https://lulustream.com"})
        if not html:
            html, _ = fetch(url, referer="https://lulustream.com/")
        if not html:
            return None

        best = _best_media_url(html)
        if best:
            return best

        for txt in _unpack_all(html):
            best = _best_media_url(txt)
            if best:
                return best

        return find_m3u8(html) or find_mp4(html)
    except Exception:
        pass
    return None


def resolve_okru(url):
    """
    ok.ru — uses the /dk/video.playJSON API (confirmed from e2iplayer parserOKRU).
    Extracts HLS manifest URL.
    """
    try:
        # Normalise URL → extract video ID
        m = re.search(r'ok\.ru/(?:video(?:embed)?/|videoembed/)(\d+)', url)
        if not m:
            m = re.search(r'/(\d{10,})', url)
        if not m:
            return None
        video_id = m.group(1)

        # API endpoint (same as e2iplayer parserOKRU)
        api_url = "https://ok.ru/dk/video.playJSON?movieId={}".format(video_id)
        mobile_ua = ("Mozilla/5.0 (iPad; U; CPU OS 3_2 like Mac OS X; en-us) "
                     "AppleWebKit/531.21.10 (KHTML, like Gecko) "
                     "Version/4.0.4 Mobile/7B334b Safari/531.21.10")
        body, _ = fetch(api_url,
                        referer=url,
                        extra_headers={
                            "User-Agent": mobile_ua,
                            "Accept": "application/json",
                        })
        if body:
            try:
                data = json.loads(body)
                hls = data.get("hlsManifestUrl", "")
                if hls:
                    return hls.replace("\\u0026", "&").replace("\\/", "/")
                # Fallback: videos array
                for vid in (data.get("videos") or []):
                    u = vid.get("url") or ""
                    if u.startswith("http"):
                        return u.replace("\\u0026", "&").replace("\\/", "/")
            except Exception:
                pass

        # Fallback: scrape embed page
        embed_url = "https://ok.ru/videoembed/{}".format(video_id)
        html, _ = fetch(embed_url, referer="https://ok.ru/",
                        extra_headers={"User-Agent": mobile_ua})
        if html:
            best = _best_media_url(html)
            if best:
                return best
            m2 = re.search(r'"hlsManifestUrl"\s*:\s*"([^"]+)"', html)
            if m2:
                return m2.group(1).replace("\\u0026", "&").replace("\\/", "/")
    except Exception:
        pass
    return None


def resolve_vidguard(url):
    """
    VidGuard / vgfplay — obfuscated JS, exposes stream_url or packed m3u8.
    """
    try:
        html, _ = fetch(url, referer="https://vidguard.to/")
        if not html:
            return None

        # Common direct patterns
        for pat in [
            r'stream_url\s*=\s*["\']([^"\']+)["\']',
            r'"(?:file|src|url)"\s*:\s*"([^"]+\.m3u8[^"]*)"',
            r"'(?:file|src|url)'\s*:\s*'([^']+\.m3u8[^']*)'",
        ]:
            m = re.search(pat, html, re.I)
            if m:
                u = m.group(1).replace("\\/", "/").replace("\\u0026", "&")
                return u

        # Packed JS
        for txt in _unpack_all(html):
            best = _best_media_url(txt)
            if best:
                return best

        # base64 decode attempts
        import base64
        for b64 in re.findall(r'atob\(["\']([A-Za-z0-9+/=]{40,})["\']\)', html, re.I):
            try:
                dec = base64.b64decode(b64 + "==").decode("utf-8", "ignore")
                best = _best_media_url(dec)
                if best:
                    return best
            except Exception:
                pass

        return find_m3u8(html) or find_mp4(html)
    except Exception:
        pass
    return None


# ── fastvid.cam resolver ──────────────────────────────────────────────────────

def resolve_fastvid(url):
    """fastvid.cam - extracts direct m3u8 URLs from the embed page"""
    try:
        html, final_url = fetch(url, referer="https://fastvid.cam/")
        if not html:
            return None
        
        # Look for master.m3u8 or index-*.m3u8 in the page
        patterns = [
            r'(https?://[^\s"\']+\.m3u8[^\s"\']*)',
            r'"(https?://[^"]+\.m3u8[^"]+)"',
            r"'(https?://[^']+\.m3u8[^']+)'",
            r'stream/([^\s"\']+\.m3u8)',
        ]
        
        found_urls = []
        for pattern in patterns:
            matches = re.findall(pattern, html, re.I)
            for match in matches:
                if match.startswith('/'):
                    # Build full URL
                    parsed = urlparse(final_url or url)
                    full_url = f"{parsed.scheme}://{parsed.netloc}{match}"
                    found_urls.append(full_url)
                elif match.startswith('http'):
                    found_urls.append(match)
        
        # Prefer master.m3u8, then index-f2 (720p), then index-f1 (480p)
        for url in found_urls:
            if 'master.m3u8' in url:
                log(f"resolve_fastvid: found master.m3u8: {url}")
                return url
        for url in found_urls:
            if 'index-f2' in url:  # 720p
                log(f"resolve_fastvid: found 720p stream: {url}")
                return url
        for url in found_urls:
            if 'index-f1' in url:  # 480p
                log(f"resolve_fastvid: found 480p stream: {url}")
                return url
        for url in found_urls:
            if '.m3u8' in url:
                log(f"resolve_fastvid: found m3u8: {url}")
                return url
        
        # Also check for JWPlayer configuration
        jw_pattern = r'file:\s*["\']([^"\']+\.m3u8[^"\']*)["\']'
        match = re.search(jw_pattern, html, re.I)
        if match:
            stream_url = match.group(1)
            if stream_url.startswith('/'):
                parsed = urlparse(final_url or url)
                stream_url = f"{parsed.scheme}://{parsed.netloc}{stream_url}"
            log(f"resolve_fastvid: found JWPlayer stream: {stream_url}")
            return stream_url
        
        return None
    except Exception as e:
        log(f"resolve_fastvid error: {e}")
        return None


# ── rpmvip / upshare / cleantechworld resolvers ──────────────────────────────

def resolve_rpmvip(url):
    """rpmvip.com - direct m3u8 URLs"""
    # These are already direct m3u8 URLs
    if '.m3u8' in url:
        return url
    try:
        html, _ = fetch(url, referer=url)
        if not html:
            return None
        return find_m3u8(html) or find_mp4(html)
    except Exception:
        return url if '.m3u8' in url else None


def resolve_upshare(url):
    """upshare / upn.one - direct m3u8 URLs"""
    if '.m3u8' in url:
        return url
    try:
        html, _ = fetch(url, referer=url)
        if not html:
            return None
        return find_m3u8(html) or find_mp4(html)
    except Exception:
        return url if '.m3u8' in url else None


def resolve_cleantechworld(url):
    """cleantechworld.shop - serves .txt files that are actually m3u8 content"""
    try:
        html, _ = fetch(url, referer=url)
        if not html:
            return None
        # If it returns m3u8 content directly, it's already a stream
        if "#EXTM3U" in html:
            return url
        # Otherwise look for m3u8 in the response
        m = re.search(r'(https?://[^\s"\']+\.m3u8[^\s"\']*)', html)
        if m:
            return m.group(1)
        return None
    except Exception as e:
        log(f"resolve_cleantechworld error: {e}")
        return None


# ========== NEW: FaselHD CDN Resolvers ==========

def resolve_scdns(url):
    """scdns.io - FaselHD's CDN for m3u8 streams (web596x.faselhdx.bid)"""
    try:
        # If it's already a direct m3u8 URL, return it
        if '.m3u8' in url:
            log("resolve_scdns: direct m3u8 URL")
            return url
        
        # Fetch the page to get the actual stream
        html, final_url = fetch(url, referer="https://www.fasel-hd.cam/")
        if html:
            # Look for m3u8 streams in various formats
            m3u8_patterns = [
                r'(https?://[^\s"\']+\.scdns\.io[^\s"\']+\.m3u8[^\s"\']*)',
                r'(https?://[^\s"\']+\.c\.scdns\.io[^\s"\']+\.m3u8[^\s"\']*)',
                r'(https?://master\.[^\s"\']+\.scdns\.io[^\s"\']+\.m3u8[^\s"\']*)',
                r'(https?://r[0-9]+--[^\s"\']+\.c\.scdns\.io[^\s"\']+\.m3u8[^\s"\']*)',
            ]
            
            for pattern in m3u8_patterns:
                matches = re.findall(pattern, html, re.I)
                for stream_url in matches:
                    stream_url = stream_url.replace('\\/', '/').replace('&amp;', '&')
                    # Prefer higher quality streams
                    if 'hd1080' in stream_url or '1080' in stream_url:
                        log("resolve_scdns: found 1080p stream")
                        return stream_url
                    elif 'hd720' in stream_url or '720' in stream_url:
                        log("resolve_scdns: found 720p stream")
                        return stream_url
            
            # Also try to find any m3u8 using the generic finder
            stream = find_m3u8(html)
            if stream:
                log("resolve_scdns: found m3u8 via generic finder")
                return stream
        
        return None
    except Exception as e:
        log(f"resolve_scdns error: {e}")
        return None


def resolve_datahowa(url):
    """datahowa.asia - CDN for govid.live streams (faselhd.rip)"""
    try:
        log("resolve_datahowa: processing {}".format(url[:80]))
        
        # If it's a segment URL, try to get the base m3u8
        if '.ts' in url:
            base_m3u8 = re.sub(r'/seg_[0-9]+\.ts.*$', '/playlist.m3u8', url)
            if base_m3u8 != url:
                log("resolve_datahowa: converting segment to playlist: {}".format(base_m3u8[:80]))
                return base_m3u8
        
        # If it's already a m3u8, return it
        if '.m3u8' in url:
            return url
        
        # Fetch to get actual stream
        html, _ = fetch(url, referer="https://faselhd.rip/")
        if html:
            m3u8 = find_m3u8(html)
            if m3u8:
                return m3u8
        
        return None
    except Exception as e:
        log(f"resolve_datahowa error: {e}")
        return None


def resolve_downet(url):
    """downet.net - Direct MP4 resolver for Akwam"""
    try:
        log("resolve_downet: processing {}".format(url[:80]))
        
        # If it's already a direct MP4 or m3u8, return it
        if '.mp4' in url or '.m3u8' in url:
            q = "HD"
            if "1080" in url:
                q = "1080p"
            elif "720" in url:
                q = "720p"
            return url
        
        # Fetch to get actual stream
        html, _ = fetch(url, referer="https://akwam.com.co/")
        if html:
            mp4 = find_mp4(html) or find_m3u8(html)
            if mp4:
                return mp4
        
        return None
    except Exception as e:
        log(f"resolve_downet error: {e}")
        return None


# ========== START: Wecima CDN Resolvers ==========

def resolve_tnmr(url):
    """tnmr.org - HLS stream resolver for Wecima"""
    try:
        html, _ = fetch(url, referer="https://wecima.cx/")
        if not html:
            return None
        # Look for master.m3u8 or direct stream
        m = re.search(r'(https?://[^\s"\']+\.tnmr\.org[^\s"\']+\.m3u8[^\s"\']*)', html)
        if m:
            return m.group(1)
        return find_m3u8(html) or find_mp4(html)
    except Exception:
        return None


def resolve_mxcontent(url):
    """mxcontent.net - MP4 stream resolver for Wecima"""
    try:
        # Direct MP4 URL - just return it
        if '.mp4' in url:
            return url
        html, _ = fetch(url, referer="https://wecima.cx/")
        if html:
            return find_mp4(html)
    except Exception:
        return None


def resolve_delucloud(url):
    """delucloud.xyz - HLS stream resolver for Wecima"""
    try:
        html, _ = fetch(url, referer="https://wecima.cx/")
        if not html:
            return None
        # Look for master.m3u8
        m = re.search(r'(https?://[^\s"\']+\.delucloud\.xyz[^\s"\']+\.m3u8[^\s"\']*)', html)
        if m:
            return m.group(1)
        return find_m3u8(html)
    except Exception:
        return None


def resolve_savefiles(url):
    """savefiles.com - HLS stream resolver for Wecima"""
    try:
        html, _ = fetch(url, referer="https://wecima.cx/")
        if not html:
            return None
        m = re.search(r'(https?://s[0-9]+\.savefiles\.com[^\s"\']+\.m3u8[^\s"\']*)', html)
        if m:
            return m.group(1)
        return find_m3u8(html)
    except Exception:
        return None


def resolve_sprintcdn(url):
    """sprintcdn.com - HLS stream resolver for Wecima"""
    try:
        html, _ = fetch(url, referer="https://wecima.cx/")
        if not html:
            return None
        return find_m3u8(html)
    except Exception:
        return None


def resolve_aurorafieldnetwork(url):
    """aurorafieldnetwork.store - HLS stream resolver for Wecima"""
    try:
        html, _ = fetch(url, referer="https://wecima.cx/")
        if not html:
            return None
        # Return the .txt file URL which may contain the actual stream
        if '.txt' in url:
            # Fetch the .txt file to get the real stream URL
            content, _ = fetch(url, referer="https://wecima.cx/")
            if content:
                m = re.search(r'(https?://[^\s"\']+\.m3u8[^\s"\']*)', content)
                if m:
                    return m.group(1)
        return find_m3u8(html)
    except Exception:
        return None

# ========== END: Wecima CDN Resolvers ==========

def resolve_savefiles(url):
    """savefiles.com - HLS stream resolver"""
    try:
        html, _ = fetch(url, referer="https://savefiles.com/")
        if not html:
            return None
        # Look for m3u8 in the page
        m = re.search(r'(https?://[^\s"\']+\.savefiles\.com[^\s"\']+\.m3u8[^\s"\']*)', html)
        if m:
            return m.group(1)
        return find_m3u8(html) or find_mp4(html)
    except Exception:
        return None


def resolve_abstream(url):
    """abstream.to - HLS stream resolver"""
    try:
        html, _ = fetch(url, referer="https://abstream.to/")
        if not html:
            return None
        return find_m3u8(html) or find_mp4(html)
    except Exception:
        return None


def resolve_byselapuix(url):
    """byselapuix.com - Filemoon variant"""
    try:
        html, _ = fetch(url, referer="https://byselapuix.com/")
        if not html:
            return None
        # Use filemoon resolver logic
        best = _best_media_url(html)
        if best:
            return best
        for txt in _unpack_all(html):
            best = _best_media_url(txt)
            if best:
                return best
        return find_m3u8(html) or find_mp4(html)
    except Exception:
        return None


def resolve_dhcplay(url):
    """dhcplay.com - Doodstream variant"""
    try:
        # Use doodstream resolver logic
        return resolve_doodstream(url)
    except Exception:
        return None
    
# Add after existing resolvers, before HOST_RESOLVERS

def resolve_go_akwam(url):
    """go.akwam.com.co - Akwam redirect resolver"""
    try:
        html, final_url = fetch(url, referer="https://akwam.com.co/")
        if not html:
            return None
        
        # Look for the final video URL in the page
        # Pattern 1: video source tag
        source_match = re.search(r'<source[^>]+src="([^"]+\.(?:mp4|m3u8)[^"]*)"', html, re.I)
        if source_match:
            return source_match.group(1)
        
        # Pattern 2: downet.net direct URL
        downet_match = re.search(r'(https?://s\d+\.downet\.net[^\s"\']+\.(?:mp4|m3u8)[^\s"\']*)', html, re.I)
        if downet_match:
            return downet_match.group(1)
        
        # Pattern 3: meta refresh
        meta_match = re.search(r'<meta[^>]+http-equiv="refresh"[^>]+content="\d+;\s*url=([^"]+)"', html, re.I)
        if meta_match:
            redirect_url = meta_match.group(1)
            if redirect_url.startswith("//"):
                redirect_url = "https:" + redirect_url
            # Recursively resolve
            return resolve_host(redirect_url, referer=url)
        
        # Pattern 4: iframe
        iframe_match = re.search(r'<iframe[^>]+src="([^"]+)"[^>]*>', html, re.I)
        if iframe_match:
            iframe_url = iframe_match.group(1)
            if iframe_url.startswith("//"):
                iframe_url = "https:" + iframe_url
            return resolve_host(iframe_url, referer=url)
        
        return None
    except Exception as e:
        log("resolve_go_akwam error: {}".format(e))
        return None


def resolve_abstream(url):
    """abstream.to - HLS stream resolver for Akwam/Wecima"""
    try:
        html, _ = fetch(url, referer="https://abstream.to/")
        if not html:
            return None
        # Look for video source or m3u8
        source_match = re.search(r'<source[^>]+src="([^"]+\.(?:mp4|m3u8)[^"]*)"', html, re.I)
        if source_match:
            return source_match.group(1)
        return find_m3u8(html) or find_mp4(html)
    except Exception:
        return None


def resolve_savefiles_akwam(url):
    """savefiles.com - HLS stream resolver for Akwam"""
    try:
        html, _ = fetch(url, referer="https://savefiles.com/")
        if not html:
            return None
        m = re.search(r'(https?://s[0-9]+\.savefiles\.com[^\s"\']+\.m3u8[^\s"\']*)', html)
        if m:
            return m.group(1)
        return find_m3u8(html) or find_mp4(html)
    except Exception:
        return None


# ─── Host dispatcher ──────────────────────────────────────────────────────────

HOST_RESOLVERS = {
    # Existing
    "streamtape":  resolve_streamtape,
    "dood":        resolve_doodstream,
    "dsvplay":     resolve_doodstream,
    "d0o0d":       resolve_doodstream,
    "doods":       resolve_doodstream,
    "ds2play":     resolve_doodstream,
    "dooood":      resolve_doodstream,
    "vidbom":      resolve_vidbom,
    "vidshare":    resolve_vidbom,
    "uqload":      resolve_uqload,
    "govid":       resolve_govid,
    "upstream":    resolve_upstream,
    "mixdrop":     resolve_mixdrop,
    "voe":         resolve_voe,
    "streamruby":  resolve_streamruby,
    "hgcloud":     resolve_hgcloud,
    "masukestin":  resolve_masukestin,
    "masukestin.com": resolve_masukestin,
    "vidtube":     resolve_vidtube,
    # New
    "streamwish":  resolve_streamwish,
    "wishfast":    resolve_streamwish,
    "filelion":    resolve_streamwish,
    "filelions":   resolve_streamwish,
    "vidhide":     resolve_streamwish,
    "streamhide":  resolve_streamwish,
    "dhtpre":      resolve_streamwish,
    "embedrise":   resolve_streamwish,
    "hglamioz":    resolve_streamwish,
    "filemoon":    resolve_filemoon,
    "lulustream":  resolve_lulustream,
    "ok.ru":       resolve_okru,
    "okru":        resolve_okru,
    "vidguard":    resolve_vidguard,
    "vgfplay":     resolve_vidguard,
    # Fastvid
    "fastvid":     resolve_fastvid,
    "fastvid.cam": resolve_fastvid,
    # RPMVip and friends
    "rpmvip":      resolve_rpmvip,
    "upshare":     resolve_upshare,
    "upn.one":     resolve_upshare,
    "cleantechworld": resolve_cleantechworld,
    "cleantechworld.shop": resolve_cleantechworld,
    
    # ========== FaselHD CDN Resolvers (NEW) ==========
    "scdns":              resolve_scdns,        # For web5106x.faselhdx.bid
    "scdns.io":           resolve_scdns,
    "c.scdns.io":         resolve_scdns,
    "datahowa":           resolve_datahowa,     # For faselhd.rip streams
    "datahowa.asia":      resolve_datahowa,
    "govid.live":         resolve_govid,        # For faselhd.rip
    
    # ========== Akwam Resolvers (NEW) ==========
    "downet":             resolve_downet,
    "downet.net":         resolve_downet,
    
    # ========== Wecima CDN Resolvers ==========
    "tnmr.org":        resolve_tnmr,
    "tnmr":            resolve_tnmr,
    "mxcontent":       resolve_mxcontent,
    "mxcontent.net":   resolve_mxcontent,
    "delucloud":       resolve_delucloud,
    "delucloud.xyz":   resolve_delucloud,
    "savefiles":       resolve_savefiles,
    "savefiles.com":   resolve_savefiles,
    "abstream":        resolve_abstream,
    "abstream.to":     resolve_abstream,
    "byselapuix":      resolve_byselapuix,
    "byselapuix.com":  resolve_byselapuix,
    "dhcplay":         resolve_dhcplay,
    "dhcplay.com":     resolve_dhcplay,
    "sprintcdn":       resolve_sprintcdn,
    "sprintcdn.com":   resolve_sprintcdn,
    "aurorafieldnetwork": resolve_aurorafieldnetwork,
    "aurorafieldnetwork.store": resolve_aurorafieldnetwork,
    
    # ========== Akwam Redirect Resolvers ==========
    "go.akwam.com.co":  resolve_go_akwam,
    "go.akwam":         resolve_go_akwam,
}


def resolve_generic_embed(url):
    """Generic resolver — m3u8/mp4 scan → packer unpack → iframe follow."""
    try:
        html, final = fetch(url, referer=url)
        if not html:
            return None
        best = _best_media_url(html)
        if best:
            return best
        for txt in _unpack_all(html):
            best = _best_media_url(txt)
            if best:
                return best
        # Follow one level of iframes
        for iframe_url in extract_iframes(html, final or url)[:3]:
            h2, _ = fetch(iframe_url, referer=url)
            if h2:
                best = _best_media_url(h2)
                if best:
                    return best
    except Exception:
        pass
    return None


# ─── Multi-provider premium resolvers (TMDB-based) ───────────────────────────

def _get_stream_moviesapi(tmdb_id, m_type, season=None, episode=None):
    url = ("https://moviesapi.club/api/v1/movies/{}".format(tmdb_id) if m_type == "movie"
           else "https://moviesapi.club/api/v1/tv/{}/{}/{}".format(tmdb_id, season or 1, episode or 1))
    body, _ = fetch(url, extra_headers={"Accept": "application/json"})
    if not body:
        return None
    try:
        data = json.loads(body)
        for src in (data.get("sources") or []):
            f = src.get("file") or src.get("url") or ""
            if ".m3u8" in f:
                return f
        for src in (data.get("sources") or []):
            f = src.get("file") or src.get("url") or ""
            if f.startswith("http"):
                return f
    except Exception:
        pass
    return find_m3u8(body) or find_mp4(body)


def _get_stream_vidsrc(tmdb_id, m_type, season=None, episode=None):
    url = ("https://vidsrc.me/embed/movie/{}".format(tmdb_id) if m_type == "movie"
           else "https://vidsrc.me/embed/tv/{}/{}/{}".format(tmdb_id, season or 1, episode or 1))
    html, _ = fetch(url, referer="https://vidsrc.me/")
    if not html:
        return None
    m = re.search(r'<iframe[^>]+src=["\']([^"\']+)["\']', html, re.I)
    if m:
        iframe_url = m.group(1)
        if iframe_url.startswith("//"):
            iframe_url = "https:" + iframe_url
        h2, _ = fetch(iframe_url, referer=url)
        if h2:
            return find_m3u8(h2) or find_mp4(h2)
    return find_m3u8(html) or find_mp4(html)


def _get_stream_autoembed(tmdb_id, m_type, season=None, episode=None):
    url = ("https://autoembed.cc/movie/tmdb-{}".format(tmdb_id) if m_type == "movie"
           else "https://autoembed.cc/tv/tmdb-{}-{}-{}".format(tmdb_id, season or 1, episode or 1))
    html, _ = fetch(url)
    if not html:
        return None
    return find_m3u8(html) or find_mp4(html)


_PREMIUM_METHODS = {
    "moviesapi": _get_stream_moviesapi,
    "vidsrc":    _get_stream_vidsrc,
    "autoembed": _get_stream_autoembed,
}


def get_premium_servers(m_type, tmdb_id, season=None, episode=None):
    suffix = ":{}:{}".format(season, episode) if (season and episode) else ""
    return [
        {"name": "Premium: AutoEmbed 🚀", "url": "autoembed://{}:{}{}".format(m_type, tmdb_id, suffix)},
        {"name": "Premium: VidSrc 🔥",    "url": "vidsrc://{}:{}{}".format(m_type, tmdb_id, suffix)},
    ]


# ─── Main host dispatcher ─────────────────────────────────────────────────────

def resolve_host(url, referer=None):
    """Detect host from domain and dispatch to the right resolver."""
    # Premium protocol shortcuts  (autoembed://, vidsrc://, etc.)
    m = re.match(r'(\w+)://(movie|series|tv|episode):(\d+)(?::(\d+):(\d+))?', url)
    if m:
        method_name, m_type, tmdb_id, season, episode = m.groups()
        m_type = "movie" if m_type in ("movie", "film") else "series"
        if method_name in _PREMIUM_METHODS:
            return _PREMIUM_METHODS[method_name](tmdb_id, m_type, season, episode)
        if method_name == "auto":
            for func in _PREMIUM_METHODS.values():
                try:
                    res = func(tmdb_id, m_type, season, episode)
                    if res:
                        return res
                except Exception:
                    pass
        return None

    domain = urlparse(url).netloc.lower()
    log("resolve_host: domain={} url={}".format(domain, url[:80]))

    # Exact-key match first, then substring scan
    for key, resolver in HOST_RESOLVERS.items():
        if key in domain:
            log("Using resolver: {}".format(key))
            result = resolver(url)
            if result:
                return result
            log("Resolver {} returned nothing, trying generic".format(key))
            break

    log("Generic fallback for: {}".format(domain))
    return resolve_generic_embed(url)


# ─── iframe chain resolver ────────────────────────────────────────────────────

def resolve_iframe_chain(url, referer=None, depth=0, max_depth=8):
    """
    Follow iframes / meta-refresh / JS location redirects recursively.
    Returns (stream_url, domain) or (None, "").
    """
    if depth > max_depth:
        return None, ""

    html, final_url = fetch(url, referer=referer)
    if not html:
        return None, ""

    active_url = final_url or url
    domain = urlparse(active_url).netloc.lower()

    # 1. Direct media URL in page
    stream = find_m3u8(html) or find_mp4(html) or find_packed_links(html)
    if stream:
        return stream, domain

    # 2. Meta-refresh redirect
    m = re.search(
        r'<meta[^>]+http-equiv=["\']refresh["\'][^>]+content=["\']\d+\s*;\s*url=([^"\']+)["\']',
        html, re.I
    )
    if m:
        new_url = m.group(1).strip()
        if new_url.startswith("//"):
            new_url = "https:" + new_url
        elif not new_url.startswith("http"):
            new_url = urljoin(active_url, new_url)
        if new_url != active_url:
            return resolve_iframe_chain(new_url, referer=active_url, depth=depth + 1, max_depth=max_depth)

    # 3. JS window.location redirect
    m = re.search(r'(?:window\.location(?:\.href)?\s*=|location\.replace\()\s*["\']([^"\']+)["\']', html, re.I)
    if m:
        new_url = m.group(1).strip()
        if new_url.startswith("//"):
            new_url = "https:" + new_url
        elif not new_url.startswith("http"):
            new_url = urljoin(active_url, new_url)
        if new_url != active_url and "://" in new_url:
            return resolve_iframe_chain(new_url, referer=active_url, depth=depth + 1, max_depth=max_depth)

    # 4. iframes (src, data-src, data-url, data-lazy-src)
    iframe_srcs = re.findall(
        r'<(?:iframe|embed|frame)[^>]+(?:src|data-src|data-url|data-lazy-src)=["\']([^"\']+)["\']',
        html, re.I
    )
    for src in iframe_srcs:
        if src.startswith("//"):
            src = "https:" + src
        elif not src.startswith("http"):
            p = urlparse(active_url)
            if src.startswith("/"):
                src = "{}://{}{}".format(p.scheme, p.netloc, src)
            else:
                continue

        if any(x in src.lower() for x in ("facebook.com", "twitter.com", "googletag", "doubleclick", "analytics")):
            continue

        # Check if this is a known host — resolve directly rather than fetching page
        src_domain = urlparse(src).netloc.lower()
        for key, resolver in HOST_RESOLVERS.items():
            if key in src_domain:
                result = resolver(src)
                if result:
                    return result, src_domain
                break

        res, h = resolve_iframe_chain(src, referer=active_url, depth=depth + 1, max_depth=max_depth)
        if res:
            return res, h

    return None, ""


# ─── Main extract_stream entry point ─────────────────────────────────────────

def extract_stream(url):
    """
    Standard entry point used by all extractors.
    Returns (stream_url, quality_label, referer).
    """
    log("--- extract_stream START: {} ---".format(url))
    raw_url = (url or "").strip()
    if not raw_url:
        return None, "", url

    # Split piped headers (url|Referer=xxx&User-Agent=yyy)
    piped_headers = {}
    main_url = raw_url
    if "|" in raw_url:
        main_url, raw_hdrs = raw_url.split("|", 1)
        for part in raw_hdrs.split("&"):
            if "=" in part:
                k, v = part.split("=", 1)
                piped_headers[k.strip()] = v.strip()

    lower = main_url.lower()

    # Fast path: already a direct media URL
    if main_url.startswith("http") and any(ext in lower for ext in (".m3u8", ".mp4", ".mkv", ".mp3", ".ts")):
        ref = piped_headers.get("Referer")
        if not ref:
            # Detect domain for proper referer
            domain = urlparse(main_url).netloc.lower()
            if "scdns.io" in domain:
                ref = "https://web5106x.faselhdx.bid/"
            elif "govid.live" in domain or "datahowa.asia" in domain:
                ref = "https://faselhd.rip/"
            else:
                ref = "{}://{}/".format(*urlparse(main_url)[:2])
        
        # Detect quality from filename
        q = "HD"
        if "1080" in lower or "fhd" in lower or "hd1080" in lower:
            q = "1080p"
        elif "720" in lower or "hd" in lower or "hd720" in lower:
            q = "720p"
        elif "480" in lower:
            q = "480p"
        elif "index-f2" in lower:
            q = "720p"
        elif "index-f1" in lower:
            q = "480p"
        elif "master.m3u8" in lower:
            q = "720p"
            
        log("extract_stream DIRECT: {}".format(main_url))
        return main_url, q, ref

    # Fetch to get final URL (for redirects)
    _, final_ref = fetch(main_url, referer=piped_headers.get("Referer"))

    # Try host resolver first
    stream = resolve_host(main_url, referer=piped_headers.get("Referer"))
    
    # If that fails, try iframe chain
    if not stream:
        log("resolve_host failed, trying iframe chain")
        stream, _ = resolve_iframe_chain(main_url, referer=piped_headers.get("Referer"))

    if stream:
        # Detect quality from stream URL
        q = "HD"
        stream_lower = stream.lower()
        if "1080" in stream_lower or "fhd" in stream_lower or "hd1080" in stream_lower:
            q = "1080p"
        elif "720" in stream_lower or "hd" in stream_lower or "hd720" in stream_lower:
            q = "720p"
        elif "480" in stream_lower:
            q = "480p"
        elif "index-f2" in stream_lower:
            q = "720p"
        elif "index-f1" in stream_lower:
            q = "480p"
            
        log("extract_stream SUCCESS: {} ({})".format(stream[:120], q))
        return stream, q, final_ref or main_url

    log("extract_stream FAILED for: {}".format(main_url))
    return None, "", final_ref or main_url