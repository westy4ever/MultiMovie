# -*- coding: utf-8 -*-
import sys
import re
from .base_scraper import BaseScraper
from ...utils.base import fetch, urljoin, log, resolve_iframe_chain, extract_stream as base_extract_stream

try:
    from urllib.parse import quote_plus, urlparse, urlunparse, quote, urlencode
    from html import unescape as html_unescape
except ImportError:
    from urllib import quote_plus, quote, urlencode
    from urlparse import urlparse, urlunparse
    from HTMLParser import HTMLParser
    html_unescape = HTMLParser().unescape

DOMAINS = ["https://topcinemaa.top"]
MAIN_URL = DOMAINS[0]

def _normalize_url(url):
    if not url:
        return ""
    url = html_unescape(url.strip())
    if url.startswith("//"):
        return "https:" + url
    if not url.startswith("http"):
        return urljoin(MAIN_URL, url)
    return url

_LEADING_TYPE_WORDS = ("فيلم", "افلام", "مسلسل", "مسلسلات", "انمي", "برنامج", "عرض")
_TITLE_NOISE_PHRASES = (
    "مشاهدة وتحميل", "مشاهدة وتحميل مباشر", "مشاهدة", "تحميل",
    "مترجمة", "مترجم", "مدبلجة", "مدبلج",
    "اون لاين", "اونلاين", "بجودة عالية", "بجودة", "حصريا", "كامل",
)

def _clean_title(title):
    """Strip bracketed tags, leading content-type words, and common noise phrases."""
    title = html_unescape(title or "")
    title = title.replace("&amp;", "&")
    title = re.sub(r'[\u200b\u200c\u200d\u200e\u200f\ufeff]+', '', title)
    title = re.sub(r'\[[^\]]*\]\s*', '', title)
    title = re.sub(r'\s*[-|]\s*ت[ةه]?وب\s*سينما\s*$', '', title, flags=re.I)
    title = re.sub(r'ت[ةه]?وب\s*سينما', '', title, flags=re.I)
    for phrase in _TITLE_NOISE_PHRASES:
        title = re.sub(r'\s*' + re.escape(phrase) + r'\s*', ' ', title, flags=re.I)
    words = title.split()
    while words and words[0] in _LEADING_TYPE_WORDS:
        words.pop(0)
    return " ".join(words).strip()

def _extract_blocks(html):
    """Extract movie/series items from listing pages."""
    items = []
    pattern = r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*title=["\']([^"\']+)["\'][^>]*>(.*?)</a>'
    for m in re.finditer(pattern, html, re.I | re.S):
        href = m.group(1)
        title = m.group(2)
        inner = m.group(3)

        if re.search(r'/(?:category|search|page|tag|author)/', href, re.I):
            continue

        img_match = re.search(r'<img[^>]+(?:data-src|src)=["\']([^"\']+)["\']', inner, re.I)
        if not img_match:
            continue
        poster = img_match.group(1)

        if not poster or poster.startswith('data:') or 'placeholder' in poster.lower():
            continue

        link = _normalize_url(href)
        poster = _normalize_url(poster)
        poster = re.sub(r'-\d+x\d+(?=\.\w+(?:\?.*)?$)', '', poster)

        item_type = "movie"
        if "مسلسل" in title or "حلقة" in title or "انمي" in title:
            item_type = "series"

        title = _clean_title(title)

        items.append({
            "title": title,
            "url": link,
            "poster": poster,
            "type": item_type,
            "_action": "details"
        })
    return items


class TopCinemaScraper(BaseScraper):
    def __init__(self):
        self.base_url = MAIN_URL

    def get_categories(self):
        """Return list of (display_name, slug_or_url)"""
        return [
            ("المضاف حديثا", "recent"),
            ("أفلام أجنبية", "category/%D8%A7%D9%81%D9%84%D8%A7%D9%85-%D8%A7%D8%AC%D9%86%D8%A8%D9%8A-8/"),
            ("أفلام أنمي", "category/%D8%A7%D9%81%D9%84%D8%A7%D9%85-%D8%A7%D9%86%D9%85%D9%8A-2/"),
            ("أفلام أسيوية", "category/%D8%A7%D9%81%D9%84%D8%A7%D9%85-%D8%A7%D8%B3%D9%8A%D9%88%D9%8A/"),
            ("أفلام نتفليكس", "netflix-movies/"),
            ("مسلسلات أجنبية", "category/%D9%85%D8%B3%D9%84%D8%B3%D9%84%D8%A7%D8%AA-%D8%A7%D8%AC%D9%86%D8%A8%D9%8A/"),
            ("مسلسلات أسيوية", "category/%D9%85%D8%B3%D9%84%D8%B3%D9%84%D8%A7%D8%AA-%D8%A7%D8%B3%D9%8A%D9%88%D9%8A%D8%A9/"),
            ("مسلسلات أنمي", "category/%D9%85%D8%B3%D9%84%D8%B3%D9%84%D8%A7%D8%AA-%D8%A7%D9%86%D9%85%D9%8A/"),
        ]

    def get_movies(self, category, page=1, filters=None):
        """Fetch movies from a category page."""
        if category == "recent":
            url = f"{self.base_url}/recent/page/{page}/"
        else:
            if not category.endswith('/'):
                category += '/'
            url = f"{self.base_url}/{category}page/{page}/"

        if filters:
            qs = "&".join([f"{k}={v}" for k, v in filters.items() if v])
            if qs:
                url += "?" + qs

        html, _ = fetch(url, referer=self.base_url)
        if not html:
            return []

        items = _extract_blocks(html)
        # Remove any pagination links that might have been extracted
        items = [i for i in items if not i.get("url", "").endswith("/page/")]
        return items

    def get_details(self, item_id_or_url):
        """Fetch details, servers, episodes for a movie/series."""
        if item_id_or_url.startswith('http'):
            url = item_id_or_url
        else:
            url = f"{self.base_url}/?p={item_id_or_url}"

        html, final_url = fetch(url, referer=self.base_url)
        if not html:
            return {}

        title_m = re.search(r'<title>(.*?)</title>', html, re.I | re.S)
        raw_title = title_m.group(1) if title_m else "Unknown Title"
        title = _clean_title(raw_title)

        poster_m = re.search(r'property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
        poster = _normalize_url(poster_m.group(1)) if poster_m else ""

        plot_m = re.search(r'class=["\']description["\'][^>]*>(.*?)</', html, re.S | re.I)
        plot = _clean_title(re.sub(r'<[^>]+>', '', plot_m.group(1))) if plot_m else ""

        watch_url_m = re.search(
            r'<a[^>]+class=["\'][^"\']*watch[^"\']*["\'][^>]+href=["\']([^"\']+/watch/?)[\"\']',
            html, re.I
        )
        watch_page_html = html
        watch_url = final_url
        if watch_url_m:
            watch_url = _normalize_url(watch_url_m.group(1))
            watch_page_html, _ = fetch(watch_url, referer=final_url)
            watch_page_html = watch_page_html or ""

        post_id = ""
        for pat in [
            r'data-id=["\'](\d+)["\']',
            r'\?p=(\d+)',
            r'postid["\']?\s*[:=]\s*["\']?(\d+)["\']?',
            r'post_id["\']?\s*[:=]\s*["\']?(\d+)["\']?'
        ]:
            m = re.search(pat, watch_page_html, re.I)
            if m:
                post_id = m.group(1)
                break

        servers = []
        server_candidates = []
        li_matches = re.findall(
            r'<li(?=[^>]*class=["\'][^"\']*server--item)(?=[^>]*data-id=["\'](\d+))(?=[^>]*data-server=["\'](\d+))[^>]*>(.*?)</li>',
            watch_page_html, re.I | re.S
        )
        for pid, idx, inner in li_matches:
            name = re.sub(r'<[^>]+>', ' ', inner)
            name = _clean_title(re.sub(r'\s+', ' ', name)).strip()
            if name:
                server_candidates.append((pid, idx, name))

        if not server_candidates:
            generic_matches = re.findall(
                r'<(?:li|a|button|div)[^>]*data-id=["\'](\d+)["\'][^>]*data-server=["\'](\d+)["\'][^>]*>(.*?)</(?:li|a|button|div)>',
                watch_page_html, re.I | re.S
            )
            for pid, idx, inner in generic_matches:
                name = re.sub(r'<[^>]+>', ' ', inner)
                name = _clean_title(re.sub(r'\s+', ' ', name)).strip()
                if name:
                    server_candidates.append((pid, idx, name))

        if not server_candidates and post_id:
            known_servers = [
                "متعدد الجودات", "UpDown", "StreamWish", "Doodstream",
                "Filelions", "Streamtape", "LuluStream", "Filemoon",
                "Mixdrop", "VidGuard", "Okru"
            ]
            for i, srv in enumerate(known_servers, 1):
                if re.search(re.escape(srv), watch_page_html, re.I):
                    server_candidates.append((post_id, str(i), srv))

        ajax_endpoint = f"{self.base_url}/wp-content/themes/movies2023/Ajaxat/Single/Server.php"
        seen = set()
        for pid, idx, name in server_candidates:
            if not pid or not idx:
                continue
            key = (pid, idx)
            if key in seen:
                continue
            seen.add(key)
            clean_name = _clean_title(name or "").strip()
            if not clean_name:
                continue
            s_url = f"topcinema_server|{ajax_endpoint}|{pid}|{idx}|{watch_url}"
            servers.append({
                "name": "توب سينما " + clean_name,
                "url": s_url,
                "quality": "",
            })

        episodes = []
        is_series_like = (
            "مسلسل" in raw_title or
            "الحلقة" in watch_page_html or
            "episodes" in watch_page_html.lower() or
            "season" in watch_page_html.lower()
        )
        if is_series_like:
            eps_container = ""
            m = re.search(
                r'<div[^>]+class=["\'][^"\']*episodes--list--side[^"\']*["\'][^>]*>(.*?)</div>',
                watch_page_html, re.S | re.I
            )
            if m:
                eps_container = m.group(1)
            else:
                for container_pat in [
                    r'<div[^>]+class=["\'][^"\']*(?:episodes|series-episodes|season-episodes|ep_list|episodes-list|series-list|all-episodes)[^"\']*["\'][^>]*>(.*?)</div>',
                    r'<ul[^>]*class=["\'][^"\']*(?:episodes|series-episodes|list-episodes|ep_list)[^"\']*["\'][^>]*>(.*?)</ul>',
                    r'<section[^>]*class=["\'][^"\']*(?:episodes|series)[^"\']*["\'][^>]*>(.*?)</section>',
                    r'<div[^>]+id=["\'][^"\']*(?:episodes|episodes-list|episodes-all)[^"\']*["\'][^>]*>(.*?)</div>'
                ]:
                    m = re.search(container_pat, watch_page_html, re.S | re.I)
                    if m:
                        eps_container = m.group(1)
                        break
            if not eps_container:
                eps_container = watch_page_html

            eps_matches = re.findall(
                r'<a[^>]+href=["\']([^"\']+/(?:watch|episode)[^"\']*)["\'][^>]*>(.*?)</a>',
                eps_container, re.DOTALL | re.I
            )
            seen_eps = set()
            for e_link, e_inner in eps_matches:
                full_link = _normalize_url(e_link)
                if not full_link or full_link == watch_url:
                    continue
                if full_link in seen_eps:
                    continue
                seen_eps.add(full_link)

                e_text = re.sub(r'<[^>]+>', '', e_inner).strip()
                e_num_m = re.search(r'الحلقة\s*(\d+)', e_text)
                if not e_num_m:
                    e_num_m = re.search(r'(\d+)', e_text)

                e_num = e_num_m.group(1).strip() if e_num_m else (e_text[:30] if e_text else "Episode")
                episodes.append({
                    "title": "حلقة " + e_num if e_num.isdigit() else e_num,
                    "url": full_link,
                    "type": "episode",
                    "_action": "item"
                })

        item_type = "series" if episodes else "movie"

        return {
            "url": final_url,
            "title": title,
            "plot": plot,
            "poster": poster,
            "servers": servers,
            "items": episodes,
            "type": item_type,
            "year": "",
            "rating": "",
            "genres": "",
        }

    def search(self, query, page=1):
        url = f"{self.base_url}/search/?query={quote_plus(query)}&type=all&page={page}"
        html, _ = fetch(url, referer=self.base_url)
        return _extract_blocks(html)

    def extract_stream(self, server_url):
        """Custom extraction for TopCinema's server AJAX."""
        log(f"TopCinemaScraper.extract_stream: {server_url}")
        if server_url.startswith("topcinema_server|"):
            parts = server_url.split("|")
            ajax_url = parts[1]
            post_id = parts[2]
            server_index = parts[3]
            referer_url = parts[4] if len(parts) > 4 else MAIN_URL

            postdata = {
                "id": post_id,
                "i": server_index
            }
            html, _ = fetch(ajax_url, referer=referer_url,
                            extra_headers={"X-Requested-With": "XMLHttpRequest"},
                            post_data=postdata)

            ifr_m = re.search(r'<iframe[^>]+src=["\']([^"\']+)["\']', html)
            if ifr_m:
                v_url = _normalize_url(ifr_m.group(1))
                log(f"TopCinema: Found iframe '{v_url}'")
                resolved = resolve_iframe_chain(v_url, referer=MAIN_URL)
                if resolved:
                    if isinstance(resolved, tuple):
                        return resolved[0], None, (resolved[1] if len(resolved) > 1 and resolved[1] else MAIN_URL)
                    return resolved, None, MAIN_URL
                return v_url, None, MAIN_URL
        # Fallback to generic extractor
        return base_extract_stream(server_url)