# -*- coding: utf-8 -*-
"""
Extractor for faselhdx.bid (FaselHD CDN variant) - MultiMovie class-based scraper.
Domains: web51212x / web5106x / web51118x / web5120x.faselhdx.bid
"""
import re
import json
import sys
from .base_scraper import BaseScraper
from ...utils.base import fetch, urljoin, log, extract_stream as base_extract_stream

try:
    from urllib.parse import quote_plus, urlparse
except ImportError:
    from urllib import quote_plus
    from urlparse import urlparse

# Stable canonical domain (the site redirects to the current CDN mirror)
BASE_URL = "https://www.fasel-hd.cam"

_KNOWN_DOMAIN_SUFFIXES = (
    "faselhdx.bid",
    "faselhd.bid",
    "fasel-hd.cam",
    "faselhd.pro",
    "faselhd.life",
)

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
       "AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/124.0.0.0 Safari/537.36")

_HEADERS = {
    "User-Agent": _UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ar,en-US;q=0.7,en;q=0.3",
    "DNT": "1",
}

_SCRIPT_NOISE = {
    "jwpcdn.com", "jwplatform.com", "unpkg.com", "cdn.jsdelivr.net",
    "cdnjs.cloudflare.com", "ajax.googleapis.com", "code.jquery.com",
    "stackpath.bootstrapcdn.com", "google-analytics.com", "googletagmanager.com",
    "aclib.net", "acscdn.com", "madurird.com", "browsecoherentunrefined.com",
    "crumpetprankerstench.com",
}

_FAKE_M3U8_HOSTS = {"img.scdns.io"}

_CARD_PAT = re.compile(
    r'<div[^>]*class="postDiv[^"]*"[^>]*>\s*'
    r'<a\s+href="([^"]+)"[^>]*>'
    r'(?:(?!<div[^>]*class="postDiv").)*?'
    r'data-src="([^"]+)"'
    r'(?:(?!<div[^>]*class="postDiv").)*?'
    r'<div[^>]*class="h1"[^>]*>([^<]+)</div>',
    re.DOTALL | re.I
)

_QUALITY_PAT = re.compile(r'<span[^>]*class="[^"]*quality[^"]*"[^>]*>([^<]+)</span>', re.I)
_IMDB_PAT = re.compile(r'<span[^>]*class="[^"]*pImdb[^"]*"[^>]*>.*?([\d.]+)', re.I | re.DOTALL)
_EPISODE_URL_PAT = re.compile(r'/(?:[a-z-]*-)?episodes/', re.I)


class FaselHDHdxScraper(BaseScraper):
    def __init__(self):
        self.base_url = BASE_URL

    def _update_base(self, url):
        """Update base URL if a valid CDN mirror is found."""
        p = urlparse(url)
        if p.netloc and any(p.netloc.lower().endswith(suf) for suf in _KNOWN_DOMAIN_SUFFIXES):
            self.base_url = "{}://{}".format(p.scheme or "https", p.netloc)

    def _norm(self, url):
        if not url:
            return ""
        url = str(url).strip().replace("&amp;", "&")
        if url.startswith("//"):
            return "https:" + url
        if not url.startswith("http"):
            return self.base_url.rstrip("/") + "/" + url.lstrip("/")
        return url

    def _clean(self, text):
        if not text:
            return ""
        text = re.sub(r'<[^>]+>', '', text)
        text = text.replace("&amp;", "&")
        text = text.replace("فاصل إعلاني", "").replace("FaselHD", "")
        text = re.sub(r'\s*[-|]\s*(فاصل\s*إعلاني|FaselHD).*$', '', text, flags=re.I)
        return text.strip()

    def _classify_type(self, item_url, title):
        """Classify a URL/title as 'episode', 'series', or 'movie'."""
        if _EPISODE_URL_PAT.search(item_url):
            return "episode"
        if "/series" in item_url or "مسلسل" in title:
            return "series"
        if "/anime" in item_url and "/anime-movies" not in item_url:
            return "series"
        return "movie"

    def _get(self, url, referer=None, extra=None):
        hdrs = dict(_HEADERS)
        hdrs["Referer"] = referer or self.base_url
        if extra:
            hdrs.update(extra)
        return fetch(url, referer=referer or self.base_url, extra_headers=hdrs)

    def _is_real_m3u8(self, url):
        host = urlparse(url).netloc.lower()
        if host in _FAKE_M3U8_HOSTS:
            return False
        if re.search(r'\.(jpg|jpeg|png|gif|webp|avif)\.m3u8', urlparse(url).path.lower()):
            return False
        return True

    def _extract_cards(self, html, max_items=50):
        """Extract cards from the id="postList" section only."""
        post_list_m = re.search(
            r'<div[^>]+id=["\']postList["\'][^>]*>(.*?)(?=<div[^>]+class="[^"]*subHead|<div[^>]+id="[^"]*footer|</div>\s*</div>\s*</div>\s*</div>\s*<div[^>]+id)',
            html, re.DOTALL | re.I
        )
        if post_list_m:
            scope = post_list_m.group(1)
            log("faselhd_hdx: scoped to postList ({} chars)".format(len(scope)))
        else:
            scope = html
            log("faselhd_hdx: postList not found, using full HTML")

        items, seen = [], set()
        for m in _CARD_PAT.finditer(scope):
            item_url = self._norm(m.group(1))
            poster = self._norm(m.group(2).split("?")[0])
            title = self._clean(m.group(3))
            card_html = m.group(0)

            if not item_url or item_url in seen or "/page/" in item_url:
                continue

            qm = _QUALITY_PAT.search(card_html)
            im = _IMDB_PAT.search(card_html)
            item_type = self._classify_type(item_url, title)

            seen.add(item_url)
            items.append({
                "title": title,
                "url": item_url,
                "poster": poster,
                "thumb": poster,
                "rating": im.group(1).strip() if im else "",
                "quality": qm.group(1).strip() if qm else "",
                "year": "",
                "type": item_type,
                "_action": "details",
            })
            if len(items) >= max_items:
                break
        return items

    def _scan_for_stream(self, html, referer):
        """Scan HTML for a playable stream URL."""
        if not html:
            return None

        # Direct m3u8 (skip img.scdns.io thumbnails)
        for m in re.finditer(r'(https?://[^\s"\'<>`\\]+\.m3u8(?:\?[^\s"\'<>`\\]*)?)', html, re.I):
            u = m.group(1).replace("\\/", "/").replace("&amp;", "&")
            if self._is_real_m3u8(u):
                return u

        # JS player config patterns
        for pat in [
            r'(?:file|src|url|source|hls)\s*[=:]\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
            r'["\']file["\']\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
        ]:
            m2 = re.search(pat, html, re.I)
            if m2:
                u = m2.group(1).replace("\\/", "/").replace("&amp;", "&")
                if self._is_real_m3u8(u):
                    return u

        # Real scdns stream host (master.c.scdns.io or r466--xxx.c.scdns.io)
        m3 = re.search(r'(https?://(?:master\.|r\d+--)[^\s"\'<>]+\.c\.scdns\.io/[^\s"\'<>]+)', html, re.I)
        if m3:
            u = m3.group(1).replace("\\/", "/").replace("&amp;", "&")
            if not u.endswith(".m3u8"):
                u = u.split("?")[0] + ".m3u8"
            return u

        # External scripts
        p = urlparse(referer)
        same_host = p.netloc
        ext_srcs = re.findall(r'<script[^>]+src=["\']?([^"\'>\s]+)["\']?', html, re.I)
        for src in ext_srcs[:8]:
            src_l = src.lower()
            if any(d in src_l for d in _SCRIPT_NOISE):
                continue
            if re.match(r'^/[a-z0-9-]+\.[a-z]{2,6}/', src_l):
                continue
            if not src.startswith("http"):
                src = self.base_url.rstrip("/") + "/" + src.lstrip("/")
            if same_host and urlparse(src).netloc != same_host:
                continue
            log("faselhd_hdx: scanning script: {}".format(src[:80]))
            js, _ = self._get(src, referer=referer)
            if not js:
                continue
            for m4 in re.finditer(r'(https?://[^\s"\'<>`\\]+\.m3u8[^\s"\'<>`\\]*)', js, re.I):
                u = m4.group(1).replace("\\/", "/")
                if self._is_real_m3u8(u):
                    return u
        return None

    # ─── BaseScraper Interface ──────────────────────────────────────────────

    def get_categories(self):
        """Return list of (display_name, full_url) tuples."""
        base = self.base_url.rstrip("/")
        return [
            ("🆕 المضاف حديثا", base + "/most_recent"),
            ("🎬 جميع الافلام", base + "/all-movies"),
            ("🎬 افلام اجنبي", base + "/movies"),
            ("🎬 افلام مدبلجة", base + "/dubbed-movies"),
            ("🎬 افلام هندي", base + "/hindi"),
            ("🎬 افلام اسيوي", base + "/asian-movies"),
            ("🎬 افلام انمي", base + "/anime-movies"),
            ("⭐ الاعلي تصويتا", base + "/movies_top_votes"),
            ("👁️ الاعلي مشاهدة", base + "/movies_top_views"),
            ("🏆 الاعلي IMDB", base + "/movies_top_imdb"),
            ("🏆 جوائز الاوسكار", base + "/oscars-winners"),
            ("🎬 سلاسل الافلام", base + "/movies_collections"),
            ("📺 جميع المسلسلات", base + "/series"),
            ("📺 المضاف حديثا (مسلسلات)", base + "/recent_series"),
            ("📺 احدث الحلقات", base + "/episodes"),
            ("📺 الاعلي مشاهدة (مسلسلات)", base + "/series_top_views"),
            ("📺 الاعلي IMDB (مسلسلات)", base + "/series_top_imdb"),
            ("📺 المسلسلات القصيرة", base + "/short_series"),
            ("📡 جميع البرامج", base + "/tvshows"),
            ("📡 المضاف حديثا (برامج)", base + "/recent_tvshows"),
            ("📡 احدث الحلقات (برامج)", base + "/tvepisodes"),
            ("📡 الاعلي مشاهدة (برامج)", base + "/tvshows_top_views"),
            ("🌏 مسلسلات اسيوي", base + "/asian-series"),
            ("🌏 المضاف حديثا (اسيوي)", base + "/recent_asian"),
            ("🌏 احدث الحلقات (اسيوي)", base + "/asian-episodes"),
            ("🌏 الاعلي مشاهدة (اسيوي)", base + "/asian_top_views"),
            ("🎌 جميع الانمي", base + "/anime"),
            ("🎌 المضاف حديثا (انمي)", base + "/recent_anime"),
            ("🎌 احدث الحلقات (انمي)", base + "/anime-episodes"),
            ("🎌 الاعلي مشاهدة (انمي)", base + "/anime_top_views"),
        ]

    def get_movies(self, category, page=1, filters=None):
        """Fetch movies from a category URL."""
        self._update_base(category)
        clean = re.sub(r'/page/\d+/?$', '', category.rstrip('/'))
        current_url = "{}/page/{}".format(clean, page) if page > 1 else clean + "/"

        html, final_url = self._get(current_url, referer=self.base_url)
        if not html:
            log("faselhd_hdx: fetch failed: {}".format(current_url))
            return []

        if final_url:
            self._update_base(final_url)

        items = self._extract_cards(html)
        log("faselhd_hdx: extracted {} items (page {})".format(len(items), page))
        return items

    def get_details(self, item_id_or_url):
        """Fetch details, servers, episodes for a movie/series."""
        url = item_id_or_url
        self._update_base(url)
        log("faselhd_hdx: get_details {}".format(url))

        html, final_url = self._get(url, referer=self.base_url)
        if not html:
            return {"title": "Error", "servers": [], "items": [], "type": "movie"}

        if final_url:
            self._update_base(final_url)

        # post_id from body class "postid-298828"
        pid_m = re.search(r'\bpostid-(\d+)\b', html)
        post_id = pid_m.group(1) if pid_m else None
        if post_id:
            log("faselhd_hdx: post_id={}".format(post_id))

        # Title
        title_m = (
            re.search(r'<div[^>]*class="[^"]*h1 title[^"]*"[^>]*>(.*?)(?:<span|</div>)', html, re.I | re.DOTALL) or
            re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']', html)
        )
        title = self._clean(title_m.group(1)) if title_m else ""

        # Poster
        poster = ""
        for pat in [
            r'itemprop=["\']image["\'][^>]+content=["\']([^"\']+)["\']',
            r'<div[^>]*class="[^"]*posterImg[^"]*"[^>]*>.*?<img[^>]+src="(https://[^"]+)"',
            r'itemprop=["\']thumbnailUrl["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
        ]:
            m = re.search(pat, html, re.I | re.DOTALL)
            if m:
                poster = self._norm(m.group(1).split("?")[0])
                break

        # Plot
        plotm = re.search(r'class="singleDesc"[^>]*>(.*?)</div>', html, re.I | re.DOTALL)
        plot = self._clean(plotm.group(1)) if plotm else ""

        # Year
        ym = (
            re.search(r'(?:سنة\s*الإنتاج|موعد الصدور)\s*:.*?(\d{4})', html, re.I | re.DOTALL) or
            re.search(r'\b(20\d{2})\b', title)
        )
        year = ym.group(1) if ym else ""

        # Rating
        rm = (
            re.search(r'class="singleStar"[^>]*>.*?<strong>([\d.]+)</strong>', html, re.I | re.DOTALL) or
            re.search(r'class="pImdb"[^>]*>.*?([\d.]+)', html, re.I | re.DOTALL)
        )
        rating = rm.group(1) if rm else ""

        # Type detection
        is_tv_content = (
            "/series" in url or "/episodes" in url or
            "مسلسل" in title or "/anime" in url
        )
        item_type = self._classify_type(url, title)

        # ── Servers ─────────────────────────────────────────────────────────────
        servers, seen_embed = [], set()

        def _add(embed_url, name=None):
            embed_url = str(embed_url).replace("&amp;", "&").replace("&#39;", "").strip()
            if not embed_url or embed_url in seen_embed:
                return
            seen_embed.add(embed_url)
            label = name or "🎬 Server {}".format(len(servers) + 1)
            servers.append({"name": label, "url": embed_url, "type": "embed"})
            log("faselhd_hdx: server {}: {}".format(len(servers), embed_url[:80]))

        tabs_m = re.search(r'<ul[^>]*class="[^"]*tabs-ul[^"]*"[^>]*>(.*?)</ul>', html, re.I | re.DOTALL)
        if tabs_m:
            tabs_html = tabs_m.group(1)
            for li_m in re.finditer(
                r'onclick=["\'][^"\']*player_iframe\.location\.href\s*=\s*'
                r'(?:&#39;|["\'])([^"\'&]+(?:&amp;[^"\'&]+)*)(?:&#39;|["\'])',
                tabs_html, re.I
            ):
                raw_url = self._norm(li_m.group(1).replace("&amp;", "&"))
                snippet = tabs_html[li_m.start():li_m.start() + 300]
                a_m = re.search(r'<a[^>]*>(.*?)</a>', snippet, re.DOTALL | re.I)
                label = "🎬 Server {}".format(len(servers) + 1)
                if a_m:
                    raw_label = re.sub(r'<[^>]+>', '', a_m.group(1)).strip()
                    if raw_label:
                        label = raw_label
                _add(raw_url, label)

        # Fallback: iframe data-src
        ifm = re.search(
            r'<iframe[^>]+name=["\']player_iframe["\'][^>]+data-src=["\']([^"\']+)["\']',
            html, re.I
        )
        if ifm and not servers:
            _add(self._norm(ifm.group(1)))

        log("faselhd_hdx: {} servers found".format(len(servers)))

        # ── Episodes ─────────────────────────────────────────────────────────────
        episodes = []
        if is_tv_content:
            for ep_m in re.finditer(
                r'<a[^>]+href="([^"]+(?:faselhdx|faselhd)[^"]+)"[^>]*>'
                r'[^<]*(?:الحلقة|Episode)\s*(\d+)',
                html, re.I
            ):
                episodes.append({
                    "title": "الحلقة {}".format(ep_m.group(2)),
                    "url": self._norm(ep_m.group(1)),
                    "type": "episode",
                    "_action": "details",
                })

        return {
            "url": final_url or url,
            "title": title,
            "plot": plot,
            "poster": poster,
            "thumb": poster,
            "year": year,
            "rating": rating,
            "servers": servers,
            "items": episodes,
            "type": item_type,
        }

    def search(self, query, page=1):
        """Search for movies/series."""
        self._update_base(self.base_url)
        url = self.base_url + "/?s=" + quote_plus(query)
        if page > 1:
            url += "&paged=" + str(page)

        html, final_url = self._get(url, referer=self.base_url)
        if not html:
            return []
        if final_url:
            self._update_base(final_url)
        return self._extract_cards(html)

    def extract_stream(self, server_url):
        """
        Resolve a faselhdx.bid server URL to a playable stream.
        """
        log("faselhd_hdx extract_stream: {}".format(server_url[:100]))
        server_url = server_url.replace("&amp;", "&").strip()
        self._update_base(server_url)

        # Already a direct m3u8
        if ".m3u8" in server_url:
            if not self._is_real_m3u8(server_url):
                log("faselhd_hdx: rejected false-positive m3u8: {}".format(server_url[:80]))
                return None, "", self.base_url
            quality = "1080p" if "1080" in server_url else ("720p" if "720" in server_url else "HD")
            return server_url, quality, self.base_url

        # video_player page
        if "video_player" in server_url or "player_token" in server_url:
            log("faselhd_hdx: fetching video_player page")
            html, final_url = self._get(server_url, referer=self.base_url)
            stream = self._scan_for_stream(html, server_url) if html else None
            if stream:
                log("faselhd_hdx: found inline stream: {}".format(stream[:80]))
                quality = "1080p" if "1080" in stream else ("720p" if "720" in stream else "HD")
                return stream, quality, server_url

            player_url = (final_url or server_url).replace("&amp;", "&")
            log("faselhd_hdx: returning player page as web link: {}".format(player_url[:80]))
            return player_url, "HD", self.base_url

        # Download/file links
        if any(d in server_url for d in ["t7meel.site", "thmeel", "srvdown", "t7hd"]):
            log("faselhd_hdx: following download link: {}".format(server_url[:80]))
            try:
                html, final = self._get(server_url, referer=self.base_url)
                if html:
                    stream = self._scan_for_stream(html, server_url)
                    if stream:
                        return stream, "HD", server_url
                resolved = (final or server_url).replace("&amp;", "&")
                return resolved, "HD", self.base_url
            except Exception as e:
                log("faselhd_hdx: download link error: {}".format(e))
                return server_url, "HD", self.base_url

        # Unknown embed — try base extractor then manual scan
        stream_url, quality, ref = base_extract_stream(server_url)
        if stream_url:
            return stream_url, quality, ref

        html, _ = self._get(server_url, referer=self.base_url)
        stream = self._scan_for_stream(html, server_url) if html else None
        if stream:
            return stream, "HD", self.base_url

        return server_url, "HD", self.base_url