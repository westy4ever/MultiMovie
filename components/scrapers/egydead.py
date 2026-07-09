# -*- coding: utf-8 -*-
"""
EgyDead extractor — WordPress site
Domain: https://tv10.egydead.live/
Converted to class‑based scraper for MultiMovie plugin.
"""

import re
import sys
from .base_scraper import BaseScraper
from ...utils.base import fetch, log, urljoin, extract_stream as base_extract_stream

try:
    from urllib.parse import quote_plus, urlparse, quote, unquote, urlencode
    from html import unescape as html_unescape
except ImportError:
    from urllib import quote_plus, urlencode
    from urlparse import urlparse, urlunparse
    from HTMLParser import HTMLParser
    html_unescape = HTMLParser().unescape

MAIN_URL = "https://tv10.egydead.live/"

_CLEAN_WORDS = [
    "مشاهدة فيلم", "مشاهدة", "فيلم", "مسلسل",
    "مترجمة اون لاين", "مترجم اون لاين",
    "مترجمة", "مترجم", "اون لاين", "أون لاين",
    "مدبلجة", "مدبلج", "كرتون", "انمي",
    "بالمصري", "سلسلة افلام", "عرض", "برنامج", "جميع مواسم",
]


def _strip_tags(text):
    text = html_unescape(text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _clean_title(title):
    title = _strip_tags(title)
    for word in _CLEAN_WORDS:
        title = title.replace(word, "")
    title = re.sub(r"\s*\|\s*$", "", title)
    title = re.sub(r"\s*\-\s*$", "", title)
    return re.sub(r"\s+", " ", title).strip(" -|")


def _full_url(path):
    if not path:
        return ""
    path = html_unescape(path.strip())
    if path.startswith("//"):
        path = "https:" + path
    elif not path.startswith("http"):
        path = urljoin(MAIN_URL, path)
    try:
        path = quote(unquote(path), safe=':/?&=#+')
    except Exception:
        pass
    return path


def _pick_real_image(html_chunk):
    """
    Find the most likely REAL image URL within a chunk of HTML, robust to
    lazy-load setups that put an identical placeholder in one attribute
    for every single image (only swapping in the real URL via JS later -
    checking every common lazy-load attribute plus plain src, and
    preferring whichever candidate actually looks like a real uploaded
    image (/wp-content/uploads/) over a same-for-every-item theme
    placeholder).
    """
    best = None
    for img_tag in re.findall(r'<img[^>]+>', html_chunk, re.I):
        tag_candidates = []
        for attr in ('data-src', 'data-lazy-src', 'data-original', 'data-lazy', 'src'):
            m = re.search(attr + r'=["\']([^"\']+)["\']', img_tag, re.I)
            if m:
                tag_candidates.append(m.group(1))
        for c in tag_candidates:
            if '/wp-content/uploads/' in c:
                return c
        if best is None and tag_candidates:
            best = tag_candidates[0]
    return best


def _encode_arabic_url(url):
    try:
        parsed = urlparse(url)
        path_segments = []
        for segment in parsed.path.split('/'):
            if segment:
                if any(ord(c) > 127 for c in segment):
                    path_segments.append(quote_plus(segment.encode('utf-8')))
                else:
                    path_segments.append(segment)
            else:
                path_segments.append('')
        encoded_path = '/'.join(path_segments)
        if not encoded_path.startswith('/'):
            encoded_path = '/' + encoded_path
        
        encoded_query = ''
        if parsed.query:
            try:
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
        
        encoded_url = parsed._replace(path=encoded_path, query=encoded_query).geturl()
        return encoded_url
    except Exception:
        return url


def _fetch(url, referer=None, post_data=None):
    extra = {}
    if post_data:
        extra["Content-Type"] = "application/x-www-form-urlencoded"
        extra["X-Requested-With"] = "XMLHttpRequest"
    
    extra["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
    extra["Accept-Language"] = "ar-EG,ar;q=0.9,en;q=0.8"
    extra["Cache-Control"] = "no-cache"
    extra["Pragma"] = "no-cache"
    extra["Sec-Fetch-Dest"] = "document"
    extra["Sec-Fetch-Mode"] = "navigate"
    extra["Sec-Fetch-Site"] = "none"
    extra["Sec-Fetch-User"] = "?1"
    extra["Upgrade-Insecure-Requests"] = "1"
    
    encoded_url = _encode_arabic_url(url)
    
    return fetch(
        encoded_url,
        referer=referer or MAIN_URL,
        extra_headers=extra if extra else None,
        post_data=post_data,
    )


def _parse_category_list(html):
    """Parse category page with movie items"""
    items = []
    seen = set()

    pattern = r'<li[^>]*class=["\'][^"\']*(?:movieItem|post-item)[^"\']*["\'][^>]*>(.*?)</li>'
    for li in re.findall(pattern, html, re.S | re.I):
        
        url_match = re.search(r'<a[^>]+href=["\']([^"\']+)["\']', li)
        if not url_match:
            continue
        
        url = _full_url(url_match.group(1))
        if not url or url in seen:
            continue
        seen.add(url)
        
        if any(x in url for x in ("/page/", "page=", "category")):
            continue
        
        title = ""
        title_match = (
            re.search(r'<h[1-3][^>]*class=["\'][^"\']*BottomTitle[^"\']*["\'][^>]*>(.*?)</h[1-3]>', li, re.S | re.I) or
            re.search(r'<h[1-3][^>]*>(.*?)</h[1-3]>', li, re.S | re.I) or
            re.search(r'<img[^>]+alt=["\']([^"\']+)["\']', li) or
            re.search(r'<a[^>]+title=["\']([^"\']+)["\']', li)
        )
        if title_match:
            title = _clean_title(title_match.group(1))
        
        poster = _pick_real_image(li)
        if poster:
            poster = _full_url(poster)
            poster = re.sub(r'-\d+x\d+(?=\.\w+$)', '', poster)
        else:
            poster = ""
        
        quality = ""
        cat_match = re.search(r'<span[^>]*class=["\'][^"\']*cat_name[^"\']*["\'][^>]*>(.*?)</span>', li, re.S | re.I)
        if cat_match:
            quality = _strip_tags(cat_match.group(1))

        raw_title_text = title_match.group(1) if title_match else ""
        url_low = url.lower()
        if any(x in url_low for x in ("/episode/", "/season/", "/serie/", "/series-category/")) or "مسلسل" in raw_title_text:
            item_type = "series"
        else:
            item_type = "movie"

        if title:
            items.append({
                "title": title,
                "url": url,
                "poster": poster,
                "plot": quality,
                "type": item_type,
                "_action": "details",
            })
    
    return items


def _parse_pagination(html, current_url):
    """Return next page item if available"""
    next_match = re.search(
        r'<a[^>]+class=["\'][^"\']*next[^"\']*(?:page-numbers)?["\'][^>]+href=["\']([^"\']+)["\']',
        html, re.I
    )
    if next_match:
        raw_href = html_unescape(next_match.group(1).strip())
        if raw_href.startswith("http"):
            next_url = raw_href
        elif raw_href.startswith("//"):
            next_url = "https:" + raw_href
        else:
            next_url = urljoin(current_url, raw_href)
        if next_url and next_url != current_url:
            return {
                "title": "➡️ Next Page",
                "url": next_url,
                "type": "category",
                "_action": "category",
            }
    return None


def _extract_detail_meta(html):
    """Extract title, poster, plot, year from item page"""
    title = ""
    title_match = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
    if title_match:
        title = _clean_title(title_match.group(1))
    
    if not title:
        title_match = re.search(r'<title>(.*?)</title>', html, re.I)
        if title_match:
            title = _clean_title(title_match.group(1).split('|')[0])
    
    poster = ""
    poster_match = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
    if poster_match and '/wp-content/uploads/' in poster_match.group(1):
        poster = _full_url(poster_match.group(1))
        poster = re.sub(r'-\d+x\d+(?=\.\w+$)', '', poster)

    if not poster:
        poster_area_match = re.search(r'<div[^>]+class=["\'][^"\']*[Pp]oster[^"\']*["\'][^>]*>(.*?)</div>', html, re.S | re.I)
        found = _pick_real_image(poster_area_match.group(1)) if poster_area_match else None
        if not found:
            found = _pick_real_image(html)
        if found:
            poster = _full_url(found)
            poster = re.sub(r'-\d+x\d+(?=\.\w+$)', '', poster)
    
    plot = ""
    desc_match = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
    if desc_match:
        plot = _strip_tags(desc_match.group(1))
    
    if not plot:
        story_match = re.search(r'<div[^>]*class=["\'][^"\']*singleStory[^"\']*["\'][^>]*>(.*?)</div>', html, re.S | re.I)
        if story_match:
            plot = _strip_tags(story_match.group(1))
    
    year = ""
    year_match = re.search(r'\b(19\d{2}|20\d{2})\b', title + " " + plot)
    if year_match:
        year = year_match.group(1)
    
    return title, poster, plot, year


def _extract_watch_servers(html, page_url):
    """
    Extract video streaming servers from EgyDead page.
    The watch servers are in <ul class="serversList"> with li elements having data-link attribute.
    """
    servers = []
    seen = set()
    
    servers_html = _find_servers_html(html)
    
    if servers_html:
        for li_match in re.finditer(r'<li[^>]*data-link=["\']([^"\']+)["\'][^>]*>(.*?)</li>', servers_html, re.S | re.I):
            video_url = html_unescape(li_match.group(1).strip())
            li_content = li_match.group(2)
            
            if not video_url or video_url in seen:
                continue
            
            if video_url.startswith("//"):
                video_url = "https:" + video_url
            seen.add(video_url)
            
            name_match = re.search(r'<span[^>]*><p[^>]*>(.*?)</p></span>', li_content, re.I) or \
                        re.search(r'<p[^>]*>(.*?)</p>', li_content, re.I) or \
                        re.search(r'<span[^>]*>(.*?)</span>', li_content, re.I)
            
            name = _strip_tags(name_match.group(1)) if name_match else f"Watch Server {len(servers) + 1}"
            
            servers.append({"name": name.strip(), "url": video_url, "type": "embed"})
    
    if not servers:
        iframe_match = re.search(r'<iframe[^>]+id=["\']videoIframe["\'][^>]+src=["\']([^"\']+)["\']', html, re.I)
        if iframe_match:
            video_url = iframe_match.group(1)
            if video_url and video_url not in seen:
                seen.add(video_url)
                servers.append({"name": "Video Player", "url": video_url, "type": "embed"})
    
    log(f"EgyDead: Found {len(servers)} watch servers for {page_url}")
    return servers


def _find_servers_html(html):
    """Extract content of <ul class="serversList"> or <ul id="watch"> from html"""
    m = re.search(
        r'<ul[^>]+class=["\'][^"\']*serversList[^"\']*["\'][^>]*>(.*?)</ul>',
        html, re.S | re.I
    )
    if not m:
        m = re.search(r'<ul[^>]*id=["\']watch["\'][^>]*>(.*?)</ul>', html, re.S | re.I)
    return m.group(1) if m else ""


class EgyDeadScraper(BaseScraper):
    def __init__(self):
        self.base_url = MAIN_URL

    def get_categories(self):
        """Return list of (display_name, url) for movies and series"""
        # We combine both movie and series categories into one flat list
        # so that the user can browse everything from the main menu.
        # The type is determined later from the URL.
        movie_cats = [
            ("🎬 English Movies", "/category/english-movies/"),
            ("🇪🇬 Arabic Movies", "/category/افلام-عربي/"),
            ("🌏 Asian Movies", "/category/افلام-اسيوية/"),
            ("🇹🇷 Turkish Movies", "/category/افلام-تركية/"),
            ("🇮🇳 Indian Movies", "/category/افلام-هندي/"),
            ("🎭 Cartoon Movies", "/category/افلام-كرتون/"),
            ("🎌 Anime Movies", "/category/افلام-انمي/"),
            ("📽️ Documentary Movies", "/category/افلام-وثائقية/"),
        ]
        series_cats = [
            ("📺 English Series", "/series-category/english-series/"),
            ("🇪🇬 Arabic Series", "/series-category/arabic-series/"),
            ("🇹🇷 Turkish Series", "/series-category/turkish-series/"),
            ("🌏 Asian Series", "/series-category/asian-series/"),
            ("🎌 Anime Series", "/series-category/anime-series/"),
            ("🎠 Cartoon Series", "/series-category/cartoon-series/"),
            ("🇮🇳 Indian Series", "/series-category/indian-series/"),
            ("📽️ Documentary Series", "/series-category/documentary-series/"),
            ("📡 TV Shows", "/series-category/tv-shows/"),
        ]
        # Combine and return as (display_name, full_url)
        return [(name, _full_url(url)) for name, url in movie_cats + series_cats]

    def get_movies(self, category, page=1, filters=None):
        """Fetch movies from a category URL."""
        # category is a full URL from get_categories
        fetch_url = category
        if page > 1:
            # Handle pagination
            if '/page/' in fetch_url:
                fetch_url = re.sub(r'/page/\d+', f'/page/{page}', fetch_url)
            elif re.search(r'[?&]page=\d+', fetch_url):
                fetch_url = re.sub(r'([?&]page=)\d+', r'\g<1>' + str(page), fetch_url)
            elif fetch_url.endswith('/'):
                fetch_url = f"{fetch_url}page/{page}/"
            else:
                fetch_url = f"{fetch_url}/page/{page}/"
        
        log(f"EgyDead: Fetching category page: {fetch_url}")
        html, final_url = _fetch(fetch_url)
        if not html:
            log(f"EgyDead: get_movies failed for {category}")
            return []

        items = _parse_category_list(html)
        # Remove any pagination links that may have been parsed as items
        items = [i for i in items if not i.get("url", "").endswith("/page/")]
        return items

    def get_details(self, item_id_or_url):
        """Fetch details, servers, episodes for a movie/series."""
        # We assume item_id_or_url is a full URL
        url = item_id_or_url
        html, final_url = _fetch(url)
        result = {
            "url": url,
            "title": "",
            "poster": "",
            "plot": "",
            "year": "",
            "rating": "",
            "servers": [],
            "items": [],
            "type": "movie",
        }

        if not html:
            log(f"EgyDead: get_details failed: {url}")
            return result

        title, poster, plot, year = _extract_detail_meta(html)
        result["title"] = title
        result["poster"] = poster
        result["plot"] = plot
        result["year"] = year

        servers = _extract_watch_servers(html, final_url or url)
        if not servers:
            log(f"EgyDead: no servers on initial load, retrying with View=1 POST: {url}")
            post_html, post_final_url = _fetch(url, post_data={"View": "1"})
            if post_html:
                servers = _extract_watch_servers(post_html, post_final_url or url)

        result["servers"] = servers

        # Determine type from URL
        low = url.lower()
        if any(x in low for x in ("/episode/", "/series/", "/season/", "/serie/", "مسلسل", "/category/مسلسلات", "/series-category/")):
            result["type"] = "series"
        else:
            result["type"] = "movie"

        log(f"EgyDead: item type={result['type']}, title={title}, watch_servers={len(servers)}")
        return result

    def search(self, query, page=1):
        """Search for movies/series"""
        search_url = MAIN_URL.rstrip("/") + "/?s=" + quote_plus(query)
        if page > 1:
            search_url += f"&paged={page}"
        
        html, final_url = _fetch(search_url)
        if not html:
            log(f"EgyDead: search failed for '{query}'")
            return []

        items = _parse_category_list(html)
        # Remove pagination links
        items = [i for i in items if not i.get("url", "").endswith("/page/")]
        return items

    def extract_stream(self, server_url):
        """Resolve a server URL to a playable stream"""
        from ...utils.base import resolve_streamruby, resolve_host, resolve_mixdrop, resolve_doodstream

        low = (server_url or "").lower()

        # StreamRuby
        if "stmruby" in low or "streamruby" in low:
            stream = resolve_streamruby(server_url)
            if stream:
                return (
                    stream + "|Referer=https://stmruby.com/&Origin=https://stmruby.com",
                    None,
                    "https://stmruby.com/",
                )

        # Mixdrop
        if "mixdrop" in low:
            stream = resolve_mixdrop(server_url)
            if stream:
                return stream, None, None

        # Doodstream
        if "dood" in low or "doodstream" in low:
            stream = resolve_doodstream(server_url)
            if stream:
                return stream, None, None

        # Govid
        if "govid.live" in low:
            try:
                from ...utils.base import resolve_govid
                stream = resolve_govid(server_url)
                if stream:
                    return stream, None, None
            except ImportError:
                pass

        # For other hosts, fallback to generic resolver
        return base_extract_stream(server_url)