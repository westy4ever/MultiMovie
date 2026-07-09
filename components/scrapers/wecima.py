# -*- coding: utf-8 -*-
import re
import sys
import base64
import json
from .base_scraper import BaseScraper
from ...utils.base import fetch, urljoin, log

try:
    from urllib.parse import quote_plus, urlparse, quote
    from html import unescape as html_unescape
except ImportError:
    from urllib import quote_plus
    from urlparse import urlparse
    from HTMLParser import HTMLParser
    html_unescape = HTMLParser().unescape

# Updated domain list - wecima.click is currently the most active
DOMAINS = [
    "https://wecima.click/",
    "https://wecima.cx/",
    "https://wecima.bid/",
    "https://www.wecima.site/",
]
VALID_HOST_MARKERS = (
    "wecima.click", "wecima.cx", "wecima.bid", "wecima.site",
)
BLOCKED_HOST_MARKERS = ("alliance4creativity.com",)
MAIN_URL = None
_HOME_HTML = None

_CATEGORY_FALLBACKS = {
    "افلام اجنبي":    "/category/foreign-movies",
    "افلام عربي":     "/category/arabic-movies",
    "مسلسلات اجنبي":  "/category/foreign-series",
    "مسلسلات عربية":  "/category/arabic-series",
    "مسلسلات انمي":   "/category/anime-series",
    "تريندج":         "/trends",
}


def _host(url):
    try:
        return (urlparse(url).netloc or "").lower()
    except Exception:
        return ""


def _is_valid_site_url(url):
    host = _host(url)
    if not host:
        return False
    if any(m in host for m in BLOCKED_HOST_MARKERS):
        return False
    return any(m in host for m in VALID_HOST_MARKERS)


def _is_blocked_page(html, final_url=""):
    text = (html or "").lower()
    final = (final_url or "").lower()
    if not text:
        return True
    if "just a moment" in text and ("cf-chl" in text or "challenge" in text):
        return True
    if "enable javascript and cookies to continue" in text:
        return True
    if "watch it legally" in text or "alliance for creativity" in text:
        return True
    if any(m in final for m in BLOCKED_HOST_MARKERS):
        return True
    return False


def _looks_like_wecima_page(html):
    text = html or ""
    return (
        "Grid--WecimaPosts" in text
        or "NavigationMenu" in text
        or "Thumb--GridItem" in text
        or "GridItem" in text
        or "List--Servers" in text
        or "WECIMA" in text
        or "وى سيما" in text
        or "wecima" in text.lower()
    )


def _site_root(url):
    parts = urlparse(url)
    return "{}://{}/".format(parts.scheme or "https", parts.netloc)


def _get_base():
    global MAIN_URL, _HOME_HTML
    if MAIN_URL:
        return MAIN_URL
    for domain in DOMAINS:
        log("Wecima: probing {}".format(domain))
        html, final_url = fetch(domain, referer=domain)
        final_url = final_url or domain
        if _is_blocked_page(html, final_url):
            log("Wecima: blocked {}".format(final_url))
            continue
        if html and _looks_like_wecima_page(html):
            MAIN_URL = _site_root(final_url)
            _HOME_HTML = html
            log("Wecima: selected base {}".format(MAIN_URL))
            return MAIN_URL
    MAIN_URL = DOMAINS[0]
    log("Wecima: fallback base {}".format(MAIN_URL))
    return MAIN_URL


def _search_url():
    return _get_base().rstrip("/") + "/?s="


def _normalize_url(url):
    if not url:
        return ""
    url = url.strip()
    try:
        url = url.encode("utf-8").decode("unicode_escape") if "\\u" in url else url
    except Exception:
        pass
    url = url.replace("\\u0026", "&").replace("&amp;", "&").replace("\\/", "/")
    url = html_unescape(url)
    if url.startswith("//"):
        return "https:" + url
    if not url.startswith("http"):
        return urljoin(_get_base(), url)
    if any(m in _host(url) for m in BLOCKED_HOST_MARKERS):
        return ""
    if _is_valid_site_url(url):
        base_parts = urlparse(_get_base())
        parts = urlparse(url)
        if parts.netloc != base_parts.netloc and any(m in parts.netloc for m in VALID_HOST_MARKERS):
            clean = "{}://{}{}".format(base_parts.scheme, base_parts.netloc, parts.path or "/")
            if parts.query:
                clean += "?" + parts.query
            return clean
    return url


def _candidate_urls(url):
    normalized = _normalize_url(url)
    if not normalized:
        return []
    parts = urlparse(normalized)
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    urls = []
    seen = set()
    seeds = []
    if MAIN_URL:
        seeds.append(MAIN_URL)
    seeds.extend(DOMAINS)
    if normalized.startswith("http"):
        seeds.insert(0, _site_root(normalized))
    for domain in seeds:
        if not domain:
            continue
        base = domain if domain.endswith("/") else domain + "/"
        candidate = urljoin(base, path.lstrip("/"))
        if candidate in seen:
            continue
        seen.add(candidate)
        urls.append(candidate)
    if normalized not in seen:
        urls.insert(0, normalized)
    return urls


def _fetch_live(url, referer=None):
    for candidate in _candidate_urls(url):
        log("Wecima: fetching {}".format(candidate))
        html, final_url = fetch(candidate, referer=referer or _get_base())
        final_url = final_url or candidate
        if _is_blocked_page(html, final_url):
            log("Wecima: blocked {}".format(final_url))
            continue
        if html and _looks_like_wecima_page(html):
            log("Wecima: success {}".format(final_url))
            return html, final_url
        if html:
            log("Wecima: page shape mismatch {}".format(final_url))
    log("Wecima: fetch failed for {}".format(url))
    return "", ""


def _clean_html(text):
    text = html_unescape(text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _clean_title(title):
    title = _clean_html(title)
    for token in (
        "مشاهدة فيلم", "مشاهدة مسلسل", "مشاهدة",
        "فيلم", "مسلسل", "اون لاين", "أون لاين",
        "مترجم", "مترجمة", "مدبلج", "مدبلجة",
    ):
        title = title.replace(token, "")
    return re.sub(r"\s+", " ", title).strip(" -|")


def _home_html():
    global _HOME_HTML
    if _HOME_HTML:
        return _HOME_HTML
    base = _get_base()
    html, final_url = _fetch_live(base, referer=base)
    _HOME_HTML = html if not _is_blocked_page(html, final_url) else ""
    return _HOME_HTML


def _guess_type(title, url):
    text = "{} {}".format(title or "", url or "").lower()
    if any(t in text for t in ("/episode/", "الحلقة", "حلقة", "/season/")):
        return "episode"
    if any(t in text for t in ("/series", "/seriestv", "مسلسل", "series-", "/season/")):
        return "series"
    return "movie"


def _grid_blocks(html):
    blocks = []
    for block in re.split(r'(?=<div[^>]+class="GridItem")', html or "", flags=re.I):
        if 'class="GridItem"' not in block:
            continue
        end_match = re.search(
            r'<ul[^>]+class="PostItemStats"[^>]*>.*?</ul>\s*</div>',
            block, re.S | re.I,
        )
        if end_match:
            blocks.append(block[: end_match.end()])
        else:
            blocks.append(block[:3000])
    return blocks


def _extract_cards(html):
    cards = []
    seen = set()
    
    for block in _grid_blocks(html):
        href_match = re.search(r'<a[^>]+href="([^"]+)"', block, re.I)
        if not href_match:
            continue
        url = _normalize_url(href_match.group(1))
        if not url or url in seen:
            continue
        
        lowered = url.lower()
        if any(t in lowered for t in ("/category/", "/tag/", "/page/", "/filtering", "/feed/", "/trends")):
            continue
        
        title_match = (
            re.search(r'<h2[^>]+class="hasyear"[^>]*itemprop="name"[^>]*>(.*?)</h2>', block, re.S | re.I) or
            re.search(r'<h2[^>]+class="hasyear"[^>]*>(.*?)</h2>', block, re.S | re.I) or
            re.search(r'title="([^"]+)"', block, re.I)
        )
        title = _clean_title(title_match.group(1) if title_match else "")
        if not title:
            continue
        
        year = ""
        year_match = re.search(r'<span[^>]+class="year"[^>]*>\(?\s*(\d{4})\s*\)?</span>', block, re.I)
        if year_match:
            year = year_match.group(1)
        
        poster = ""
        poster_match = re.search(r'data-src="([^"]+)"', block, re.I)
        if poster_match:
            poster = poster_match.group(1)
        if not poster:
            poster_match = re.search(r'data-lazy-style="[^"]*url\(([^)]+)\)"', block, re.I)
            if poster_match:
                poster = poster_match.group(1).strip("'\" ")
        if not poster:
            poster_match = re.search(r'style="[^"]*--image:url\(([^)]+)\)', block, re.I)
            if poster_match:
                poster = poster_match.group(1).strip("'\" ")
        
        seen.add(url)
        cards.append({
            "title": title,
            "url": url,
            "poster": _normalize_url(poster) if poster else "",
            "plot": year,
            "type": _guess_type(title, url),
            "_action": "details",
        })
    
    log("Wecima: extracted {} cards".format(len(cards)))
    return cards


def _extract_next_page(html):
    patterns = [
        r'<a[^>]+class="[^"]*next[^"]*page-numbers[^"]*"[^>]+href="([^"]+)"',
        r'<a[^>]+rel="next"[^>]+href="([^"]+)"',
        r'<a[^>]+href="([^"]+)"[^>]*>»</a>',
    ]
    for pat in patterns:
        m = re.search(pat, html or "", re.I)
        if m:
            return _normalize_url(m.group(1))
    return ""


def _category_from_home(label, fallback):
    html = _home_html()
    for pattern in (
        r'<a[^>]+href="([^"]+)"[^>]*>\s*' + re.escape(label) + r'\s*</a>',
        r'<a[^>]+href="([^"]+)"[^>]*>\s*<span[^>]*>\s*' + re.escape(label) + r'\s*</span>',
    ):
        m = re.search(pattern, html or "", re.S | re.I)
        if m:
            url = _normalize_url(m.group(1))
            if url:
                return url
    return _normalize_url(urljoin(_get_base(), fallback))


def _decode_wecima_url(encoded):
    """
    Decode Wecima's obfuscated URLs.
    """
    if not encoded:
        return None

    log("Wecima: decoding: {}".format(repr(encoded[:80])))

    # Primary: the confirmed current scheme (strip "+" junk, restore the missing "aHR0c" prefix)
    try:
        cleaned = encoded.strip().replace(' ', '+').replace('+', '')
        cleaned = re.sub(r'[^A-Za-z0-9/=]', '', cleaned)
        fixed = 'aHR0c' + cleaned
        missing_padding = len(fixed) % 4
        if missing_padding:
            fixed += '=' * (4 - missing_padding)
        decoded_bytes = base64.b64decode(fixed)
        decoded_url = decoded_bytes.decode('utf-8', errors='replace')
        decoded_url = decoded_url.replace('\\u0026', '&').replace('\\/', '/')
        if decoded_url.startswith('http://') or decoded_url.startswith('https://'):
            log("Wecima: decode success (prefix scheme): {}".format(decoded_url[:80]))
            return decoded_url
    except Exception as e:
        log("Wecima: prefix-scheme decode failed: {}".format(str(e)[:50]))

    # Fallback: try the string as plain, unmodified base64
    try:
        cleaned = encoded.strip().replace(' ', '+')
        cleaned = re.sub(r'[^A-Za-z0-9+/=]', '', cleaned)
        missing_padding = len(cleaned) % 4
        if missing_padding:
            cleaned += '=' * (4 - missing_padding)
        decoded_bytes = base64.b64decode(cleaned)

        for encoding in ('ascii', 'utf-8', 'latin-1'):
            try:
                decoded_url = decoded_bytes.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            decoded_url = decoded_bytes.decode('ascii', errors='replace')

        decoded_url = decoded_url.replace('\\u0026', '&').replace('\\/', '/')
        decoded_url = quote(decoded_url, safe=':/?&=#+')

        if decoded_url.startswith('//'):
            decoded_url = 'https:' + decoded_url
        elif decoded_url.startswith('https') and not decoded_url.startswith('https://'):
            decoded_url = 'https://' + decoded_url[5:]
        elif decoded_url.startswith('http') and not decoded_url.startswith('http://'):
            decoded_url = 'http://' + decoded_url[4:]

        if decoded_url and ('http://' in decoded_url or 'https://' in decoded_url):
            log("Wecima: decode success (plain b64 fallback): {}".format(decoded_url[:80]))
            return decoded_url
    except Exception as e:
        log("Wecima: plain-b64 decode failed: {}".format(str(e)[:50]))

    # Last resort: try to extract a plain URL pattern directly
    url_pattern = r'[a-zA-Z0-9\-]+\.(?:com|net|org|tv|cx|bid|site|click|show|video|rent|date|live|rip|top|xyz|ps)(?:/[a-zA-Z0-9\-_/]+)?'
    match = re.search(url_pattern, encoded)
    if match:
        url = "https://" + match.group(0)
        log("Wecima: extracted URL pattern: {}".format(url))
        return url

    log("Wecima: decode failed entirely for: {}".format(repr(encoded[:80])))
    return None


def _extract_servers(html):
    """
    Robust server extraction for the new WeCima 'WatchServersList' layout.
    """
    servers = []
    seen = set()
    
    if not html:
        log("Wecima: empty HTML in _extract_servers")
        return []

    # 1. Targeted Extraction: Isolate the server list container first
    server_block_match = re.search(r'class="WatchServersList">(.*?)</ul>', html, re.S)
    
    if server_block_match:
        content = server_block_match.group(1)
        items = re.findall(r'data-url="([^"]+)"[^>]*>(.*?)<\/(?:btn|li|div)>', content, re.S)
        
        for encoded_url, inner_html in items:
            decoded_url = _decode_wecima_url(encoded_url)
            if not decoded_url or not decoded_url.startswith('http'):
                continue
                
            if decoded_url not in seen:
                name_match = re.search(r'<strong>(.*?)</strong>', inner_html)
                server_name = name_match.group(1).strip() if name_match else "Wecima Server"
                
                seen.add(decoded_url)
                servers.append({"name": server_name, "url": decoded_url, "type": "direct"})
                log("Wecima: Found server '{}' -> {}".format(server_name, decoded_url[:60]))

    # 2. Fallback Logic: Deep scan if the targeted block wasn't found
    if not servers:
        log("Wecima: Targeted block not found, running deep scan fallback...")
        fallback_items = re.findall(r'data-url="([a-zA-Z0-9+/=]{20,})"', html)
        for encoded_url in fallback_items:
            decoded_url = _decode_wecima_url(encoded_url)
            if decoded_url and decoded_url.startswith('http') and decoded_url not in seen:
                seen.add(decoded_url)
                servers.append({"name": "Server Fallback", "url": decoded_url, "type": "direct"})

    if not servers:
        log("Wecima: ERROR - No servers found. The site layout may have changed.")
    else:
        log("Wecima: Successfully extracted {} servers".format(len(servers)))
        
    return servers


def _extract_episode_cards(html):
    episodes = []
    seen = set()
    for card in _extract_cards(html):
        title = card.get("title") or ""
        url = card.get("url") or ""
        if "الحلقة" not in title and "حلقة" not in title and "/episode/" not in url.lower():
            continue
        if url in seen:
            continue
        seen.add(url)
        episodes.append({
            "title": title or "حلقة",
            "url": url,
            "type": "episode",
            "_action": "details",
        })
    return episodes


def _parse_json_ld(html):
    json_ld_match = re.search(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', html or "", re.S | re.I)
    if not json_ld_match:
        return None
    
    try:
        data = json.loads(json_ld_match.group(1))
        return data
    except Exception:
        return None


def _detail_title(html):
    data = _parse_json_ld(html)
    if data:
        if isinstance(data, dict):
            if data.get("name"):
                return _clean_title(data["name"])
            if "@graph" in data:
                for item in data["@graph"]:
                    if item.get("name") and ("فيلم" in item.get("name", "") or "مسلسل" in item.get("name", "")):
                        return _clean_title(item["name"])
    
    patterns = [
        r'<h1[^>]+itemprop="name"[^>]*>(.*?)</h1>',
        r'<h1[^>]+class="[^"]*title[^"]*"[^>]*>(.*?)</h1>',
        r'<h1[^>]*>(.*?)</h1>',
        r'property="og:title"[^>]+content="([^"]+)"',
    ]
    for pattern in patterns:
        m = re.search(pattern, html or "", re.S | re.I)
        if m:
            title = _clean_title(m.group(1))
            if title:
                return title
    return ""


def _detail_plot(html):
    data = _parse_json_ld(html)
    if data:
        if isinstance(data, dict):
            if data.get("description"):
                desc = _clean_html(data["description"])
                if desc and len(desc) > 30:
                    return desc
            if "@graph" in data:
                for item in data["@graph"]:
                    if item.get("description"):
                        desc = _clean_html(item["description"])
                        if desc and len(desc) > 30:
                            return desc
    
    patterns = [
        r'<meta[^>]+itemprop="description"[^>]+content="([^"]+)"',
        r'property="og:description"[^>]+content="([^"]+)"',
        r'name="description"[^>]+content="([^"]+)"',
        r'<div[^>]+class="StoryMovieContent"[^>]*>(.*?)</div>',
    ]
    for pattern in patterns:
        m = re.search(pattern, html or "", re.S | re.I)
        if m:
            text = _clean_html(m.group(1))
            if text and "موقع وي سيما" not in text.lower() and len(text) > 30:
                return text
    return ""


def _detail_poster(html):
    data = _parse_json_ld(html)
    if data:
        if isinstance(data, dict):
            if data.get("image") and isinstance(data["image"], dict):
                poster = data["image"].get("url", "")
                if poster:
                    return _normalize_url(poster)
            if "@graph" in data:
                for item in data["@graph"]:
                    if item.get("image") and isinstance(item["image"], dict):
                        poster = item["image"].get("url", "")
                        if poster:
                            return _normalize_url(poster)
                    if item.get("thumbnailUrl"):
                        return _normalize_url(item["thumbnailUrl"])
    
    patterns = [
        r'property="og:image"[^>]+content="([^"]+)"',
        r'<meta[^>]+itemprop="thumbnailUrl"[^>]+content="([^"]+)"',
        r'data-lazy-style="[^"]*--img:url\(([^)]+)\)',
        r'data-src="([^"]+)"',
    ]
    for pattern in patterns:
        m = re.search(pattern, html or "", re.I)
        if m:
            poster = m.group(1).strip("'\" ")
            if poster:
                return _normalize_url(poster) or poster
    return ""


def _detail_year(title, html):
    data = _parse_json_ld(html)
    if data:
        if isinstance(data, dict):
            if data.get("datePublished"):
                year_match = re.search(r'(\d{4})', data["datePublished"])
                if year_match:
                    return year_match.group(1)
            if "@graph" in data:
                for item in data["@graph"]:
                    if item.get("datePublished"):
                        year_match = re.search(r'(\d{4})', item["datePublished"])
                        if year_match:
                            return year_match.group(1)
    
    m = re.search(r'<span[^>]+class="year"[^>]*>\(?\s*(\d{4})\s*\)?</span>', html or "", re.I)
    if m:
        return m.group(1)
    m = re.search(r'\b(19\d{2}|20\d{2})\b', title or "")
    if m:
        return m.group(1)
    return ""


def _detail_rating(html):
    data = _parse_json_ld(html)
    if data:
        if isinstance(data, dict):
            if "aggregateRating" in data:
                rating = data["aggregateRating"].get("ratingValue", "")
                if rating:
                    return str(rating)
            if "@graph" in data:
                for item in data["@graph"]:
                    if "aggregateRating" in item:
                        rating = item["aggregateRating"].get("ratingValue", "")
                        if rating:
                            return str(rating)
    
    m = re.search(r'"ratingValue"\s*:\s*"?(\\?\d+(?:\.\d+)?)', html or "", re.I)
    if m:
        return m.group(1).replace("\\", "")
    m = re.search(r'(\d+(?:\.\d+)?)\s*/\s*10', html or "", re.I)
    if m:
        return m.group(1)
    return ""


def get_categories_legacy(mtype="movie"):
    """Legacy function kept for compatibility; not used by class."""
    pass


class WecimaScraper(BaseScraper):
    def __init__(self):
        self.base_url = _get_base()

    def get_categories(self):
        """Return list of (display_name, full_url) for all categories."""
        # We combine both movie and series categories from the home page.
        cats = [
            ("أفلام أجنبية",   _category_from_home("افلام اجنبي",   _CATEGORY_FALLBACKS["افلام اجنبي"])),
            ("أفلام عربية",    _category_from_home("افلام عربي",    _CATEGORY_FALLBACKS["افلام عربي"])),
            ("مسلسلات أجنبية", _category_from_home("مسلسلات اجنبي", _CATEGORY_FALLBACKS["مسلسلات اجنبي"])),
            ("مسلسلات عربية",  _category_from_home("مسلسلات عربية", _CATEGORY_FALLBACKS["مسلسلات عربية"])),
            ("كارتون وانمي",   _category_from_home("مسلسلات انمي",  _CATEGORY_FALLBACKS["مسلسلات انمي"])),
            ("ترند",           _category_from_home("تريندج",        _CATEGORY_FALLBACKS["تريندج"])),
        ]
        # Filter out empty URLs and return tuples
        return [(name, url) for name, url in cats if url]

    def get_movies(self, category, page=1, filters=None):
        """Fetch movies from a category URL."""
        # category is a full URL from get_categories
        if page > 1:
            # Build paginated URL
            if '/page/' in category:
                paged_url = re.sub(r'/page/\d+', f'/page/{page}', category)
            elif re.search(r'[?&]page=\d+', category):
                paged_url = re.sub(r'([?&]page=)\d+', r'\g<1>' + str(page), category)
            else:
                if category.endswith('/'):
                    paged_url = category + f'page/{page}/'
                else:
                    paged_url = category + f'?page={page}'
        else:
            paged_url = category

        html, final_url = _fetch_live(paged_url, referer=self.base_url)
        if _is_blocked_page(html, final_url):
            log(f"Wecima: category blocked {category}")
            return []

        items = _extract_cards(html)
        # Remove any "next page" item that might be included
        items = [i for i in items if not i.get("url", "").endswith("/page/")]
        return items

    def get_details(self, item_id_or_url):
        """Fetch details, servers, episodes for a movie/series."""
        # item_id_or_url is a full URL
        url = item_id_or_url
        html, final_url = _fetch_live(url, referer=self.base_url)
        if _is_blocked_page(html, final_url) or not html:
            log(f"Wecima: detail failed {url}")
            return {"title": "Error", "servers": [], "items": [], "type": "movie"}

        title = _detail_title(html)
        poster = _detail_poster(html)
        plot = _detail_plot(html)
        year = _detail_year(title, html)
        rating = _detail_rating(html)

        servers = _extract_servers(html)
        episodes = [] if servers else _extract_episode_cards(html)
        log(f"Wecima: detail {url} -> servers={len(servers)}, episodes={len(episodes)}")

        item_type = _guess_type(title, final_url or url)
        if episodes:
            item_type = "series"
        elif servers and any(t in (title or "") for t in ("الحلقة", "حلقة")):
            item_type = "episode"

        return {
            "url": final_url or url,
            "title": title,
            "plot": plot,
            "poster": poster,
            "rating": rating,
            "year": year,
            "servers": servers,
            "items": episodes,
            "type": item_type,
        }

    def search(self, query, page=1):
        """Search for movies/series."""
        items = []
        html = ""
        base = self.base_url
        for search_url in [
            _search_url() + quote_plus(query),
            urljoin(base, "search/") + quote_plus(query),
        ]:
            if page > 1:
                search_url += f"&page={page}" if "?" in search_url else f"?page={page}"
            html, final_url = _fetch_live(search_url, referer=base)
            if _is_blocked_page(html, final_url):
                continue
            items = _extract_cards(html)
            if items:
                break

        log(f"Wecima: search '{query}' -> {len(items)} items")
        # Remove any "next page" item
        items = [i for i in items if not i.get("url", "").endswith("/page/")]
        return items

    def extract_stream(self, server_url):
        """
        Extract the final playable URL from a Wecima server embed link.
        Returns (stream_url, quality_label, referer).
        """
        from ...utils.base import extract_stream as base_extract_stream
        
        log(f"Wecima: extract_stream for {server_url}")
        
        # First, try the base extractor
        try:
            stream_url, quality, ref = base_extract_stream(server_url)
            if stream_url:
                final_ref = ref or self.base_url
                log(f"Wecima: base_extract_stream returned: {stream_url[:80]} (quality: {quality}, referer: {final_ref})")
                return stream_url, quality, final_ref
        except Exception as e:
            log(f"Wecima: base_extract_stream error: {str(e)[:50]}")

        # Fallback 1: try to resolve the embed page ourselves
        log("Wecima: trying manual extraction fallback")
        try:
            html, final_url = fetch(server_url, referer=self.base_url)
            if html:
                from ...utils.base import find_m3u8, find_mp4, _best_media_url
                stream = find_m3u8(html) or find_mp4(html) or _best_media_url(html)
                if stream:
                    log(f"Wecima: manual fallback found: {stream[:80]}")
                    return stream, "HD", (final_url or server_url)
        except Exception as e:
            log(f"Wecima: manual fallback error: {str(e)[:50]}")

        # Fallback 2: just return the URL itself
        log(f"Wecima: returning original URL as last resort: {server_url}")
        return server_url, "Unknown", self.base_url