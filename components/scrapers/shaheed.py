# -*- coding: utf-8 -*-
"""
Shaheed4u extractor - MultiMovie class-based scraper.
Supports: Movies, Series, TV Shows, Wrestling Shows.
"""
import re
import sys
import json
import time
from .base_scraper import BaseScraper
from ...utils.base import fetch, urljoin, log, resolve_iframe_chain, extract_stream as base_extract_stream

try:
    from urllib.parse import quote_plus, urlparse, quote
    from html import unescape as html_unescape
except ImportError:
    from urllib import quote_plus, quote
    from urlparse import urlparse
    from HTMLParser import HTMLParser
    html_unescape = HTMLParser().unescape

DOMAINS = [
    "https://shaied4u.co/",
    "https://shahidd4u.com/",   # fallback
]
VALID_HOST_MARKERS = ("shaied4u.co", "shahidd4u.com")
BLOCKED_HOST_MARKERS = ("alliance4creativity.com",)
_HOME_HTML = None
_HOME_LAST_FETCH = 0


def _host(url):
    try:
        return (urlparse(url).netloc or "").lower()
    except Exception:
        return ""


def _is_blocked_page(html, final_url=""):
    text = (html or "").lower()
    final = (final_url or "").lower()
    if not text:
        return True
    if "just a moment" in text and "cf-chl" in text:
        return True
    if "cf-turnstile" in text:
        return True
    if "challenge" in text and "cloudflare" in text:
        return True
    if "access denied" in text or "blocked" in text:
        return True
    if "alliance for creativity" in text:
        return True
    if any(m in final for m in BLOCKED_HOST_MARKERS):
        return True
    return False


def _is_valid_category_page(html):
    if not html:
        return False
    if 'class="show-card"' in html:
        return True
    if re.search(r'<a[^>]+class="[^"]*show-card[^"]*"', html, re.I):
        return True
    if '<title>' in html and ('افلام' in html or 'مسلسلات' in html):
        if not _is_blocked_page(html):
            return True
    return False


def _site_root(url):
    parts = urlparse(url)
    return "{}://{}/".format(parts.scheme or "https", parts.netloc)


def _normalize_url(url, base=None):
    if not url:
        return ""
    url = html_unescape(url.strip())
    if url.startswith("//"):
        return "https:" + url
    if not url.startswith("http"):
        if base:
            return urljoin(base, url)
        return urljoin(_get_base(), url)
    return url


def _get_base(force_refresh=False):
    global _HOME_HTML, _HOME_LAST_FETCH
    if hasattr(_get_base, "_cached_base") and not force_refresh and (time.time() - _HOME_LAST_FETCH) < 21600:
        return _get_base._cached_base

    for domain in DOMAINS:
        log("Shaheed: probing {}".format(domain))
        html, final_url = fetch(domain, referer=domain)
        final_url = final_url or domain
        if _is_blocked_page(html, final_url):
            log("Shaheed: blocked {}".format(final_url))
            continue
        if html and ("شاهد" in html or "shahid" in html.lower() or "film" in html.lower()):
            base = _site_root(final_url)
            _HOME_HTML = html
            _HOME_LAST_FETCH = time.time()
            _get_base._cached_base = base
            log("Shaheed: selected base {}".format(base))
            return base

    base = DOMAINS[0]
    _get_base._cached_base = base
    log("Shaheed: fallback base {}".format(base))
    return base


def _fetch_live(url, referer=None):
    """Fetch a page, and if it appears to be a challenge, refresh the base and retry."""
    ref = referer or _get_base()
    html, final_url = fetch(url, referer=ref)
    if _is_blocked_page(html, final_url):
        log("Shaheed: blocked page, refreshing base and retrying")
        _get_base(force_refresh=True)
        html, final_url = fetch(url, referer=_get_base())
        if _is_blocked_page(html, final_url):
            return "", ""
    return html, final_url or url


def _extract_servers_from_html(html, final_url):
    """Extract server list from HTML (film or watch page)."""
    servers = []
    # Primary method: JSON.parse('...')
    pattern = r'let\s+servers\s*=\s*JSON\.parse\(\'([^\']+)\'\)'
    match = re.search(pattern, html)
    if match:
        try:
            servers_json = match.group(1).replace('\\"', '"')
            servers_data = json.loads(servers_json)
            for server in servers_data:
                if server.get("url"):
                    servers.append({
                        "name": server.get("name", "Server"),
                        "url": server["url"],
                        "type": "embed"
                    })
            log("Shaheed: extracted {} servers from JSON".format(len(servers)))
            return servers
        except Exception as e:
            log("Shaheed: failed to parse servers JSON: {}".format(e))
    # Fallback: look for iframes
    skip_domains = ['youtube', 'facebook', 'twitter', 'google', 'doubleclick',
                    'analytics', 'googletagmanager', 'cloudflareinsights',
                    'adsco.re', 'intelligenceadx']
    embed_domains = ['fastvid.cam', 'streamtape', 'doodstream', 'voe',
                     'filemoon', 'rpmvip', 'upn.one', 'cleantechworld',
                     'streamwish', 'mixdrop', 'vidguard']
    for iframe_match in re.finditer(r'<iframe[^>]+src=["\']([^"\']+)["\']', html, re.I):
        iframe_url = iframe_match.group(1)
        if any(x in iframe_url.lower() for x in skip_domains):
            continue
        if iframe_url.startswith("//"):
            iframe_url = "https:" + iframe_url
        elif iframe_url.startswith("/"):
            p = urlparse(final_url or "")
            if p.netloc:
                iframe_url = "{}://{}{}".format(p.scheme, p.netloc, iframe_url)
        if any(d in iframe_url.lower() for d in embed_domains):
            servers.append({"name": "Embed Player", "url": iframe_url, "type": "iframe"})
    return servers


class ShaheedScraper(BaseScraper):
    def __init__(self):
        self.base_url = _get_base()
        # Cache the base URL for future calls
        self._cached_base = self.base_url

    def get_categories(self):
        """Return list of (display_name, full_url) tuples."""
        base = self.base_url.rstrip("/")
        return [
            ("🎬 افلام اجنبي", base + "/category/افلام-اجنبي"),
            ("🎬 افلام عربي", base + "/category/افلام-عربي"),
            ("🎬 افلام هندي", base + "/category/افلام-هندي"),
            ("🎬 افلام انمي", base + "/category/افلام-انمي"),
            ("🎬 افلام تركية", base + "/category/افلام-تركية"),
            ("📺 مسلسلات اجنبي", base + "/category/مسلسلات-اجنبي"),
            ("📺 مسلسلات تركية", base + "/category/مسلسلات-تركية"),
            ("📺 مسلسلات انمي", base + "/category/مسلسلات-انمي"),
            ("📺 مسلسلات مدبلجة", base + "/category/مسلسلات-مدبلجة"),
            ("📺 مسلسلات عربي", base + "/category/مسلسلات-عربي"),
            ("📺 مسلسلات هندية", base + "/category/مسلسلات-هندية"),
            ("📺 مسلسلات اسيوية", base + "/category/مسلسلات-اسيوية"),
            ("🤼 عروض مصارعة", base + "/category/عروض-مصارعة"),
            ("📺 برامج تلفزيونية", base + "/category/برامج-تلفزيونية"),
            ("🌙 مسلسلات رمضان 2026", base + "/category/مسلسلات-رمضان-2026"),
        ]

    def get_movies(self, category, page=1, filters=None):
        """Fetch movies from a category URL."""
        # Build paginated URL
        url = category
        if page > 1:
            sep = "&" if "?" in url else "?"
            url += sep + "page=" + str(page)

        html, _ = _fetch_live(url)
        if not html:
            return []

        if not _is_valid_category_page(html):
            log("Shaheed: category page appears invalid, refreshing base and retrying")
            _get_base(force_refresh=True)
            html, _ = _fetch_live(url)
            if not html or not _is_valid_category_page(html):
                return []

        items = []
        seen_urls = set()

        # Extract show-card entries
        for match in re.finditer(r'<a\s[^>]*class="[^"]*show-card[^"]*"[^>]*>(.*?)</a>', html, re.DOTALL | re.I):
            tag_open = html[match.start():match.start() + 300]
            card_content = match.group(1)

            href_m = re.search(r'href="([^"]+)"', tag_open, re.I)
            if not href_m:
                continue
            full_url = _normalize_url(href_m.group(1), self.base_url)
            if not full_url or full_url in seen_urls:
                continue
            seen_urls.add(full_url)

            poster_url = ""
            poster_m = re.search(r'background-image:\s*url\(([^)]+)\)', tag_open + card_content, re.I)
            if poster_m:
                poster_url = _normalize_url(poster_m.group(1).strip("'\" "), self.base_url)

            title_m = re.search(r'<p[^>]*class="[^"]*title[^"]*"[^>]*>([^<]+)</p>', card_content, re.I)
            if not title_m:
                title_m = re.search(r'<[^>]+class="[^"]*title[^"]*"[^>]*>([^<]+)</', card_content, re.I)
            if not title_m:
                title_m = re.search(r'>([^<]{3,})<', card_content)
            title = html_unescape(title_m.group(1).strip()) if title_m else ""
            if not title:
                continue

            quality_m = re.search(r'<span[^>]*class="[^"]*sticker[^"]*"[^>]*>([^<]+)</span>', card_content, re.I)
            quality = quality_m.group(1).strip() if quality_m else ""

            categ_m = re.search(r'<span[^>]*class="[^"]*categ[^"]*"[^>]*>([^<]+)</span>', card_content, re.I)
            category_name = categ_m.group(1).strip() if categ_m else ""

            item_type = "series" if ("مسلسلات" in category_name or "عروض" in category_name or
                                      "/category/مسلسلات" in url or "/category/عروض" in url) else "movie"

            display_title = "{} [{}]".format(title, quality) if quality else title
            items.append({
                "title": display_title,
                "url": full_url,
                "poster": poster_url,
                "plot": category_name,
                "type": item_type,
                "_action": "details",
            })

        # Fallback: generic cards
        if not items:
            log("Shaheed: show-card pattern matched 0 items, trying generic card fallback")
            for match in re.finditer(
                r'<(?:article|div)[^>]+class="[^"]*(?:card|item|post|movie)[^"]*"[^>]*>(.*?)</(?:article|div)>',
                html, re.S | re.I
            ):
                block = match.group(1)
                href_m = re.search(r'href="([^"]+)"', block, re.I)
                if not href_m:
                    continue
                full_url = _normalize_url(href_m.group(1), self.base_url)
                if not full_url or full_url in seen_urls:
                    continue
                seen_urls.add(full_url)

                title_m = (re.search(r'<h[1-4][^>]*>([^<]+)</h[1-4]>', block, re.I) or
                           re.search(r'alt="([^"]+)"', block, re.I) or
                           re.search(r'title="([^"]+)"', block, re.I))
                title = html_unescape(title_m.group(1).strip()) if title_m else ""
                if not title:
                    continue

                img_m = (re.search(r'src="([^"]+\.(?:jpg|jpeg|png|webp)[^"]*)"', block, re.I) or
                         re.search(r'data-src="([^"]+)"', block, re.I))
                poster_url = _normalize_url(img_m.group(1), self.base_url) if img_m else ""

                items.append({
                    "title": title,
                    "url": full_url,
                    "poster": poster_url,
                    "type": "movie",
                    "_action": "details",
                })

        log("Shaheed: category {} -> {} items".format(category, len(items)))
        return items

    def get_details(self, item_id_or_url):
        """Fetch details, servers, episodes for a movie/series."""
        url = item_id_or_url
        html, final_url = _fetch_live(url)

        result = {
            "url": final_url or url,
            "title": "",
            "plot": "",
            "poster": "",
            "servers": [],
            "items": [],
            "type": "movie",
        }

        if not html:
            log("Shaheed: get_details failed for {}".format(url))
            return result

        # Basic metadata
        title_match = re.search(r'<title>(.*?)</title>', html)
        if title_match:
            title = html_unescape(title_match.group(1))
            title = re.sub(r'\s*[-|]\s*شاهد\s*فور\s*يو.*$', '', title)
            title = re.sub(r'\s*[-|]\s*Shahid4u.*$', '', title, flags=re.I)
            result["title"] = title.strip()

        desc_match = re.search(r'<meta\s+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
        if desc_match:
            result["plot"] = html_unescape(desc_match.group(1))

        poster_match = re.search(r'<meta\s+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
        if poster_match:
            result["poster"] = _normalize_url(poster_match.group(1), self.base_url)

        # Try to extract servers from the current page
        servers = _extract_servers_from_html(html, final_url)
        if servers:
            result["servers"] = servers
        else:
            # Try watch page
            log("Shaheed: no servers on film page, trying watch page")
            watch_url = url.rstrip('/') + '/watch/'
            watch_html, watch_final = _fetch_live(watch_url)
            if watch_html:
                servers = _extract_servers_from_html(watch_html, watch_final)
                if servers:
                    result["servers"] = servers
                    log("Shaheed: found {} servers on watch page".format(len(servers)))
                else:
                    log("Shaheed: no servers found on watch page either")
            else:
                log("Shaheed: failed to fetch watch page")

        if "/مسلسلات" in url or "series" in url.lower() or "/عروض" in url or "/post/" in url:
            result["type"] = "series"

        log("Shaheed: {} -> found {} servers".format(url, len(result["servers"])))
        return result

    def search(self, query, page=1):
        """Search for movies/series."""
        base = self.base_url.rstrip("/")
        url = base + "/search?s=" + quote_plus(query)
        if page > 1:
            url += "&page=" + str(page)
        html, _ = _fetch_live(url)
        if not html:
            return []
        # Reuse the parsing logic from get_movies but without category pagination
        # We'll use the same parsing as get_movies but we cannot call get_movies with a search URL as category.
        # So we extract items manually using the same parsing code.
        items = []
        seen_urls = set()
        for match in re.finditer(r'<a\s[^>]*class="[^"]*show-card[^"]*"[^>]*>(.*?)</a>', html, re.DOTALL | re.I):
            tag_open = html[match.start():match.start() + 300]
            card_content = match.group(1)
            href_m = re.search(r'href="([^"]+)"', tag_open, re.I)
            if not href_m:
                continue
            full_url = _normalize_url(href_m.group(1), self.base_url)
            if not full_url or full_url in seen_urls:
                continue
            seen_urls.add(full_url)
            poster_url = ""
            poster_m = re.search(r'background-image:\s*url\(([^)]+)\)', tag_open + card_content, re.I)
            if poster_m:
                poster_url = _normalize_url(poster_m.group(1).strip("'\" "), self.base_url)
            title_m = re.search(r'<p[^>]*class="[^"]*title[^"]*"[^>]*>([^<]+)</p>', card_content, re.I) or \
                      re.search(r'<[^>]+class="[^"]*title[^"]*"[^>]*>([^<]+)</', card_content, re.I) or \
                      re.search(r'>([^<]{3,})<', card_content)
            title = html_unescape(title_m.group(1).strip()) if title_m else ""
            if not title:
                continue
            quality_m = re.search(r'<span[^>]*class="[^"]*sticker[^"]*"[^>]*>([^<]+)</span>', card_content, re.I)
            quality = quality_m.group(1).strip() if quality_m else ""
            categ_m = re.search(r'<span[^>]*class="[^"]*categ[^"]*"[^>]*>([^<]+)</span>', card_content, re.I)
            category_name = categ_m.group(1).strip() if categ_m else ""
            item_type = "series" if ("مسلسلات" in category_name or "عروض" in category_name) else "movie"
            display_title = "{} [{}]".format(title, quality) if quality else title
            items.append({
                "title": display_title,
                "url": full_url,
                "poster": poster_url,
                "plot": category_name,
                "type": item_type,
                "_action": "details",
            })
        # Fallback generic cards if needed
        if not items:
            for match in re.finditer(r'<(?:article|div)[^>]+class="[^"]*(?:card|item|post|movie)[^"]*"[^>]*>(.*?)</(?:article|div)>', html, re.S | re.I):
                block = match.group(1)
                href_m = re.search(r'href="([^"]+)"', block, re.I)
                if not href_m:
                    continue
                full_url = _normalize_url(href_m.group(1), self.base_url)
                if not full_url or full_url in seen_urls:
                    continue
                seen_urls.add(full_url)
                title_m = re.search(r'<h[1-4][^>]*>([^<]+)</h[1-4]>', block, re.I) or \
                          re.search(r'alt="([^"]+)"', block, re.I) or \
                          re.search(r'title="([^"]+)"', block, re.I)
                title = html_unescape(title_m.group(1).strip()) if title_m else ""
                if not title:
                    continue
                img_m = re.search(r'src="([^"]+\.(?:jpg|jpeg|png|webp)[^"]*)"', block, re.I) or \
                        re.search(r'data-src="([^"]+)"', block, re.I)
                poster_url = _normalize_url(img_m.group(1), self.base_url) if img_m else ""
                items.append({
                    "title": title,
                    "url": full_url,
                    "poster": poster_url,
                    "type": "movie",
                    "_action": "details",
                })
        log("Shaheed: search '{}' -> {} items".format(query, len(items)))
        return items

    def extract_stream(self, server_url):
        """Resolve a server URL to a playable stream."""
        log("Shaheed extract_stream: {}".format(server_url))
        referer = self.base_url
        if "|" in server_url:
            parts = server_url.split("|", 1)
            server_url = parts[0]
            if "Referer=" in parts[1]:
                referer = parts[1].split("Referer=")[1].strip()

        stream, _ = resolve_iframe_chain(server_url, referer=referer, max_depth=10)
        if stream:
            return stream, None, referer

        # Fallback: try to resolve via base extractor
        return base_extract_stream(server_url)