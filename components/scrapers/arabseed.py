# -*- coding: utf-8 -*-
import base64
import html as html_lib
import json
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from .base_scraper import BaseScraper
from ...utils.base import fetch, log, urljoin, clear_cookies, extract_stream as base_extract_stream

# FIX: asd.pics no longer matches the site's canonical domain (confirmed via
# canonical tags/og:url on every sample page pointing to arabseeds.cam). If
# asd.pics is intentionally kept as an entry/gateway domain that redirects to
# arabseeds.cam, this can be reverted - but as a direct base for urljoin() it
# should be the real domain.
MAIN_URL     = "https://arabseeds.cam/"
QUALITY_ORDER = {"1080": 0, "720": 1, "480": 2}
BLOCKED_HOSTS = ("vidara.to", "bysezejataos.com")


def _clean_title(title):
    # FIX: unescape all HTML entities (e.g. &#8217;) instead of only &amp;
    return (
        html_lib.unescape(title or "")
        .replace("مشاهدة", "")
        .replace("فيلم", "")
        .strip()
    )


def _extract_first(patterns, text):
    for pattern in patterns:
        match = re.search(pattern, text or "", re.S)
        if match:
            return match.group(1).strip()
    return ""


def _decode_hidden_url(url):
    # FIX: when the AJAX response's own "server" field is empty (confirmed
    # in logs - this happens for the "سيرفر عرب سيد" entry specifically),
    # urljoin(MAIN_URL, "") resolved to the bare site homepage
    # ("https://arabseeds.cam/") instead of being recognized as invalid.
    # That bogus URL was then offered as a selectable server that could
    # never play anything (extract_stream has nothing to extract from a
    # homepage) - showing up as a dead/black-screen option in the list.
    if not (url or "").strip():
        return ""
    url = (url or "").replace("\\/", "/").replace("&amp;", "&").strip()
    if url.startswith("//"):
        url = "https:" + url
    if not url.startswith("http"):
        url = urljoin(MAIN_URL, url)
    for key in ("url", "id"):
        marker = key + "="
        if marker not in url:
            continue
        raw = url.split(marker, 1)[1].split("&", 1)[0]
        try:
            raw += "=" * ((4 - len(raw) % 4) % 4)
            decoded = base64.b64decode(raw).decode("utf-8")
            if decoded.startswith("http"):
                return decoded
        except Exception:
            pass
    # FIX: if nothing decoded and we're left with just the bare site root
    # (no real path), treat that as "no server" rather than a valid result.
    if url.rstrip("/") == MAIN_URL.rstrip("/"):
        return ""
    return url


def _server_priority(server_url):
    lowered = server_url.lower()
    if "reviewrate" in lowered or "reviewtech" in lowered:
        return 0
    if "vidmoly" in lowered:
        return 1
    # FIX: confirmed real final-file hosts (found via manual IDM capture) -
    # prioritize direct-file hosts since they need no further resolution,
    # ahead of the generic/unknown bucket.
    if "downet.net" in lowered:
        return 2
    if "mxcontent.net" in lowered:
        return 3
    return 9


def _server_name(server_url, label_hint=""):
    lowered = (server_url or "").lower()
    if "reviewrate" in lowered or "reviewtech" in lowered:
        return "عرب سيد"
    if "vidmoly" in lowered:
        return "VidMoly"
    if "downet.net" in lowered:
        return "Downet (Direct)"
    if "mxcontent.net" in lowered:
        return "MxContent"
    if label_hint:
        return label_hint.strip()
    domain_match = re.search(r'https?://([^/]+)', server_url or "")
    return domain_match.group(1) if domain_match else "Server"


def _collect_ajax_servers(watch_html, watch_url):
    # FIX: "always the exact same episode no matter what, even after
    # replacing files and restarting the plugin" points at session state
    # that outlives a plugin restart - the module-level cookiejar in
    # base.py only resets on a full device reboot. If the backend trusts a
    # session cookie over the post_id we send in the AJAX body for
    # deciding which item's servers to return, a stale cookie from a much
    # earlier visit would pin every subsequent request to that old item
    # indefinitely. Clear cookies for this domain before every fresh
    # server-resolution pass to rule that out.
    try:
        clear_cookies("arabseeds.cam")
    except Exception:
        pass

    token = _extract_first(
        [
            r"csrf__token['\"]?\s*[:=]\s*['\"]([^'\"]+)",
            r"csrf_token['\"]?\s*[:=]\s*['\"]([^'\"]+)",
        ],
        watch_html,
    )
    post_id = _extract_first(
        [
            r"psot_id['\"]?\s*[:=]\s*['\"](\d+)",
            r"post_id['\"]?\s*[:=]\s*['\"](\d+)",
        ],
        watch_html,
    )
    # FIX: real markup is `main__obj = {\n'home__url': '...'`; the missing
    # \s* after the opening brace meant this never matched and always fell
    # back to MAIN_URL.
    home_url = _extract_first([r"main__obj\s*=\s*\{\s*'home__url':\s*'([^']+)'"], watch_html) or MAIN_URL
    if not token or not post_id:
        log("ArabSeed: Missing AJAX token/post_id")
        return []

    quality_url     = urljoin(home_url, "get__quality__servers/")
    watch_server_url = urljoin(home_url, "get__watch__server/")
    results = []
    seen    = set()
    lock    = threading.Lock()

    def _cache_bust(u):
        # FIX: different episodes were coming back with identical response
        # sizes/content for this same endpoint URL (post_id in the POST
        # body ignored), which matches a CDN/WAF/backend cache keyed only
        # on URL - one episode's cached response getting served for every
        # other episode. Give every request a unique URL to defeat that.
        sep = "&" if "?" in u else "?"
        return "{}{}_cb={}{:04d}".format(u, sep, int(time.time() * 1000), random.randint(0, 9999))

    def fetch_row(row_post_id, server_id, row_quality, label):
        watch_body, _ = fetch(
            _cache_bust(watch_server_url),
            post_data={
                "post_id":   row_post_id,
                "quality":   row_quality,
                "server":    server_id,
                "csrf_token": token,
            },
            referer=watch_url,
        )
        if not watch_body:
            return None
        try:
            watch_data = json.loads(watch_body)
        except Exception:
            return None
        if watch_data.get("type") != "success" or not watch_data.get("server"):
            return None

        server_url_decoded = _decode_hidden_url(watch_data.get("server", ""))
        if not server_url_decoded.startswith("http"):
            return None
        if any(h in server_url_decoded for h in BLOCKED_HOSTS):
            return None
        return {
            "quality": row_quality,
            "url":     server_url_decoded,
            "name":    _server_name(server_url_decoded, label),
        }

    def fetch_quality(quality):
        local_results = []
        body, _ = fetch(
            _cache_bust(quality_url),
            post_data={"post_id": post_id, "quality": quality, "csrf_token": token},
            referer=watch_url,
        )
        if not body:
            return local_results
        try:
            data = json.loads(body)
        except Exception:
            log("ArabSeed: Failed to decode quality JSON for {}p".format(quality))
            return local_results
        if data.get("type") != "success":
            return local_results

        # Direct server in response
        direct_server = _decode_hidden_url(data.get("server", ""))
        if direct_server.startswith("http") and not any(h in direct_server for h in BLOCKED_HOSTS):
            local_results.append({
                "quality": quality,
                "url":     direct_server,
                "name":    _server_name(direct_server, "سيرفر عرب سيد"),
            })

        # Server list rows - FIX: these used to be fetched one at a time
        # (each taking 7-27s on this site), stacking up sequential wait time
        # across every row of every quality tier. Fire them concurrently
        # instead so wall-clock time is roughly the slowest single request,
        # not the sum of all of them.
        server_rows = re.findall(
            r'<li[^>]+data-post="([^"]+)"[^>]+data-server="([^"]+)"[^>]+data-qu="([^"]+)"[^>]*>.*?<span>([^<]+)</span>',
            data.get("html", ""),
            re.S,
        )
        if server_rows:
            # FIX: capped at 6 before, which combined with 3 concurrent
            # quality tiers could spike to 18 simultaneous connections to
            # this flaky origin - likely contributing to some episodes
            # hanging/never resolving. 3 is still a solid speedup over fully
            # sequential without hammering the server as hard.
            with ThreadPoolExecutor(max_workers=min(3, len(server_rows))) as ex:
                for row_result in ex.map(lambda r: fetch_row(*r), server_rows):
                    if row_result:
                        local_results.append(row_result)
        return local_results

    # FIX: the three quality tiers are independent requests - fetch them
    # concurrently rather than one after another for the same reason as above.
    with ThreadPoolExecutor(max_workers=3) as ex:
        for tier_results in ex.map(fetch_quality, ("1080", "720", "480")):
            for item in tier_results:
                key = (item["quality"], item["url"])
                with lock:
                    if key in seen:
                        continue
                    seen.add(key)
                results.append(item)

    # FIX: if AJAX returned nothing at all, log clearly rather than silent empty
    if not results:
        log("ArabSeed: AJAX returned 0 servers for watch_url={}".format(watch_url))

    results.sort(key=lambda item: (
        QUALITY_ORDER.get(item["quality"], 9),
        _server_priority(item["url"]),
        item["name"],
    ))
    return results


def _get_categories_original():
    # FIX: previous slugs (foreign-movies-12, arabic-movies-12,
    # foreign-series-5, arabic-series-10, anime-series-1, wwe-shows-1) no
    # longer exist on the site and would 404. The site now organizes content
    # under /category/films/, /category/tv/, /category/anime/ with the
    # subcategories below - verified against arabseeds_cam-home.html,
    # films.html, english_movie_category.html, english_movies-page_2.html,
    # movie.html, and links.html (all show the same nav consistently).
    return [
        {"title": "🎬 كل الأفلام",       "url": urljoin(MAIN_URL, "category/films/"),                 "type": "category", "_action": "category"},
        {"title": "🌍 أفلام أجنبي",      "url": urljoin(MAIN_URL, "category/films/foreign-movies/"),  "type": "category", "_action": "category"},
        {"title": "🌏 أفلام آسيوية",     "url": urljoin(MAIN_URL, "category/films/asian-movies/"),    "type": "category", "_action": "category"},
        {"title": "🇮🇳 أفلام هندي",      "url": urljoin(MAIN_URL, "category/films/indian-movies/"),   "type": "category", "_action": "category"},
        {"title": "🇹🇷 أفلام تركي",      "url": urljoin(MAIN_URL, "category/films/turkish-movies/"),  "type": "category", "_action": "category"},
        {"title": "📺 كل المسلسلات",     "url": urljoin(MAIN_URL, "category/tv/"),                    "type": "category", "_action": "category"},
        {"title": "📺 مسلسلات أجنبي",    "url": urljoin(MAIN_URL, "category/tv/foreign-series/"),     "type": "category", "_action": "category"},
        {"title": "🇮🇳 مسلسلات هندي",    "url": urljoin(MAIN_URL, "category/tv/indian-tv-series/"),   "type": "category", "_action": "category"},
        {"title": "🇹🇷 مسلسلات تركي",    "url": urljoin(MAIN_URL, "category/tv/turkish-series/"),     "type": "category", "_action": "category"},
        {"title": "🎭 أفلام انمي",       "url": urljoin(MAIN_URL, "category/anime/anime-movies/"),    "type": "category", "_action": "category"},
        {"title": "🎭 مسلسلات انمي",     "url": urljoin(MAIN_URL, "category/anime/anime-series/"),    "type": "category", "_action": "category"},
    ]


def _get_category_items_original(url):
    html, _ = fetch(url, referer=MAIN_URL)
    if not html:
        return []

    items = []
    seen  = set()

    # FIX: previously matched only by accident (the "item" alternative
    # happened to match the unrelated "item__contents" wrapper div's class
    # substring). Target the real anchor class used on the site
    # ("movie__block", confirmed on category pages) directly, keeping the
    # old alternatives as a fallback for older/other layouts. Also capture
    # the class attribute itself (not just inner HTML) since it carries the
    # "is__episode" marker needed for correct series detection below.
    blocks = re.findall(
        r'<a[^>]+class=["\']([^"\']*(?:movie__block|recent--block|post--block)[^"\']*)["\'][^>]*>(.*?)</a>',
        html, re.S | re.IGNORECASE
    )
    if not blocks:
        blocks = [("", b) for b in re.findall(
            r'(<a[^>]+href=["\'][^>]*>.*?<img[^>]+(?:data-src|src)=["\'][^>]*>.*?</a>)',
            html, re.S | re.IGNORECASE
        )]

    for class_attr, block in blocks:
        m = (
            re.search(r'<a[^>]+href=["\']([^"\']+)["\'][^>]+title=["\']([^"\']+)["\'][^>]*>', block, re.S) or
            re.search(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>.*?<img[^>]+alt=["\']([^"\']+)["\']', block, re.S)
        )
        if m:
            link, title = m.groups()
            img_m = re.search(r'<img[^>]+(?:data-src|src)=["\']([^"\']+)["\']', block)
            img   = img_m.group(1) if img_m else ""
            if link in seen or "/category/" in link:
                continue
            seen.add(link)
            title     = _clean_title(title)
            # FIX: these category tiles link directly to one specific
            # episode (e.g. "mslsl-westworld-season-3-episode-1/"), each
            # with its own servers - they are NOT a series-hub page needing
            # further episode selection. plugin.py already has full support
            # for an "episode" item type (its own label, direct-open
            # handling) - but this was tagging them "series" instead, which
            # made tapping one specific episode tile open the full episode
            # picker again rather than going straight to that episode's
            # servers. Use "episode" whenever the URL itself identifies a
            # specific episode; reserve "series" for genuine hub links
            # (e.g. "/series-slug/" with no season/episode marker) that
            # still need an episode-selection step.
            if "-season-" in link or "-episode-" in link:
                item_type = "episode"
            elif "is__episode" in class_attr or "/series-" in link or "مسلسل" in title or "الحلقة" in title:
                item_type = "series"
            else:
                item_type = "movie"
            items.append({"title": title, "url": link, "poster": img, "type": item_type, "_action": "details"})

    # Broad fallback if nothing found yet
    if not items:
        regex = r'<a[^>]+href=["\']([^"\']+)["\'][^>]+title=["\']([^"\']+)["\'][^>]*>.*?<img[^>]+(?:data-src|src)=["\']([^"\']+)["\']'
        for link, title, img in re.findall(regex, html, re.S | re.IGNORECASE):
            if link in seen or "/category/" in link:
                continue
            seen.add(link)
            if "-season-" in link or "-episode-" in link:
                item_type = "episode"
            elif "/series-" in link or "مسلسل" in title or "الحلقة" in title:
                item_type = "series"
            else:
                item_type = "movie"
            items.append({"title": title.strip(), "url": link, "poster": img, "type": item_type, "_action": "details"})

    # FIX: prefer an explicit rel="next" link (used by the site's own
    # pagination head tag) over the first bare /page/N/ match, so this
    # can't accidentally latch onto an unrelated page link if the DOM
    # order ever changes.
    next_page = (
        re.search(r'<link[^>]+rel=["\']next["\'][^>]+href=["\']([^"\']+/page/\d+/)["\']', html) or
        re.search(r'href="([^"]+/page/\d+/)"', html)
    )
    if next_page:
        # We do NOT append a "next page" item here; the MultiMovie plugin handles pagination via the page parameter.
        pass
    return items


def _get_page_original(url):
    html, final_url = fetch(url, referer=MAIN_URL)
    if not html:
        return {"title": "Error", "servers": []}

    result = {
        "url":     final_url or url,
        "title":   "",
        "plot":    "",
        "poster":  "",
        "rating":  "",
        "year":    "",
        "servers": [],
        "items":   [],
    }

    # FIX: og:title includes site branding/junk (e.g. "... اون لاين | عرب سيد")
    # while <h1> is the clean "Title ( Year )" form - prefer h1 so both the
    # cleaned title and year-from-title extraction work correctly.
    title_match = (
        re.search(r'<h1[^>]*>(.*?)</h1>', html, re.S) or
        re.search(r'og:title[^>]+content="([^"]+)"', html)
    )
    if title_match:
        result["title"] = _clean_title(title_match.group(1).split("-")[0])

    poster_match = re.search(r'og:image"[^>]+content="([^"]+)"', html)
    if poster_match:
        result["poster"] = poster_match.group(1)

    plot_match = re.search(r'name="description"[^>]+content="([^"]+)"', html)
    if plot_match:
        result["plot"] = plot_match.group(1)

    # FIX: these fields were declared in the result dict but never actually
    # extracted. Rating renders as e.g. <div class="post__ratings">6.0</div>,
    # year is embedded in the title like "Immortal Combat ( 2026 )".
    rating_match = re.search(r'class="post__ratings">\s*([\d.]+)\s*</div>', html)
    if rating_match:
        result["rating"] = rating_match.group(1)

    year_match = re.search(r'\(\s*(\d{4})\s*\)', result["title"])
    if year_match:
        result["year"] = year_match.group(1)

    # FIX: real episode URLs look like ".../mslsl-westworld-season-3-episode-1/"
    # - "season-"/"episode-" are preceded by a hyphen, not a slash, so the
    # old "/season-"/"/episode-" checks never matched and every episode page
    # was treated as a movie. Also added "الحلقة" (episode) as a title-based
    # signal alongside "مسلسل" (series/drama).
    is_series = (
        any(m in (final_url or url) for m in ("/series-", "-season-", "-episode-"))
        or "مسلسل" in result["title"]
        or "الحلقة" in result["title"]
    )

    # Determine watch URL
    watch_url   = (final_url or url).rstrip("/") + "/watch/"
    watch_match = re.search(r'href="([^"]+/watch/)"', html)
    if watch_match:
        watch_url = watch_match.group(1)

    watch_html, watch_final = fetch(watch_url, referer=final_url or url)
    if not watch_html:
        watch_html, watch_final = html, (final_url or url)

    for server in _collect_ajax_servers(watch_html, watch_final or watch_url):
        result["servers"].append({
            "name": "[{}p] {}".format(server["quality"], server["name"]),
            "url":  server["url"],
            "type": "direct",
        })

    if is_series:
        seen_eps = set()
        # FIX: the old container class names (Blocks-Episodes, Episode--List,
        # etc.) don't exist on the current site, so this always fell back to
        # scanning the *entire* page - which also matched a "most searched"
        # sidebar widget containing episode-1 links from unrelated shows.
        # The real container is `<ul class="episodes__list ...">`, and its
        # `<a>` tags carry no title attribute at all - the episode number is
        # in a separate `<div class="epi__num">الحلقة<b>N</b></div>`.
        container_match = re.search(
            r'<ul[^>]+class=["\'][^"\']*episodes__list[^"\']*["\'][^>]*>(.*?)</ul>',
            html, re.S | re.I
        )
        if container_match:
            container = container_match.group(1)
            for ep_url, ep_num in re.findall(
                r'<a[^>]+href="(https?://[^/]+/[^"]+)"[^>]*>.*?<div[^>]+class="epi__num">[^<]*<b>(\d+)</b></div>',
                container, re.S
            ):
                if ep_url in seen_eps:
                    continue
                seen_eps.add(ep_url)
                # Build a per-episode title by swapping the episode number
                # into this page's own title (e.g. "Westworld الموسم الثالث
                # الحلقة 1" -> "... الحلقة 8"), since the list itself has no
                # per-item title text.
                ep_title, n_subs = re.subn(r'(الحلقة\s*)\d+', r'\g<1>' + ep_num, result["title"])
                if n_subs == 0 and ep_title:
                    ep_title = "{} - الحلقة {}".format(result["title"], ep_num)
                result["items"].append({
                    "title":   ep_title.strip(),
                    "url":     ep_url,
                    "type":    "episode",
                    "_action": "details",
                })
        else:
            # Fallback for other layouts that do use title-bearing anchors.
            for ep_url, ep_title in re.findall(
                r'<a[^>]+href="(https?://[^/]+/[^"]+)"[^>]+title="([^"]+)"',
                html, re.S
            ):
                if ("الحلقة" not in ep_title and "حلقة" not in ep_title) or ep_url in seen_eps:
                    continue
                if not any(x in ep_url for x in ("series-", "-season", "episode")):
                    continue
                seen_eps.add(ep_url)
                result["items"].append({
                    "title":   ep_title.strip(),
                    "url":     ep_url,
                    "type":    "episode",
                    "_action": "details",
                })

    # Data-link fallback if AJAX produced nothing
    # FIX: this previously matched any data-src on the page (including
    # unrelated poster/thumbnail images from a "related movies" section),
    # returning them as fake "servers". Skip obvious image URLs and only
    # accept data-src/data-href (not data-link/url/iframe, which are more
    # specific to actual server/player markup) when scoped this broadly.
    if not result["servers"]:
        IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg")
        for fallback in re.findall(r'data-(?:link|url|iframe|src|href)="([^"]+)"', watch_html or "", re.S):
            fallback = _decode_hidden_url(fallback)
            if not fallback.startswith("http"):
                continue
            if fallback.lower().split("?", 1)[0].endswith(IMAGE_EXT):
                continue
            if any(h in fallback for h in BLOCKED_HOSTS):
                continue
            if fallback not in [s["url"] for s in result["servers"]]:
                result["servers"].append({"name": "Fallback", "url": fallback, "type": "direct"})

    return result


class ArabseedScraper(BaseScraper):
    def __init__(self):
        self.base_url = MAIN_URL

    def get_categories(self):
        """Return list of (display_name, full_url) tuples."""
        cats = _get_categories_original()
        return [(cat["title"], cat["url"]) for cat in cats]

    def get_movies(self, category, page=1, filters=None):
        """Fetch movies from a category URL."""
        # category is a full URL from get_categories
        if page > 1:
            # Check if URL already has a /page/ segment and replace it, else append.
            if '/page/' in category:
                paged_url = re.sub(r'/page/\d+', f'/page/{page}', category)
            else:
                # Ensure trailing slash before appending
                if not category.endswith('/'):
                    category += '/'
                paged_url = category + f'page/{page}/'
        else:
            paged_url = category

        items = _get_category_items_original(paged_url)
        # The original function may have added a "next page" item; we remove it.
        items = [i for i in items if not i.get("url", "").endswith("/page/")]
        return items

    def get_details(self, item_id_or_url):
        """Fetch details, servers, episodes for a movie/series."""
        # item_id_or_url is a full URL
        return _get_page_original(item_id_or_url)

    def search(self, query, page=1):
        """Search for movies/series."""
        # Build search URL
        search_url = urljoin(MAIN_URL, "?s=" + html_lib.escape(query))
        if page > 1:
            # The site uses ?page=N for search results
            if '?' in search_url:
                search_url += f"&page={page}"
            else:
                search_url += f"?page={page}"

        html, _ = fetch(search_url, referer=MAIN_URL)
        if not html:
            return []

        # Use same parsing as category (the search results page uses the same layout)
        items = _get_category_items_original(search_url)
        # Remove any "next page" item
        items = [i for i in items if not i.get("url", "").endswith("/page/")]
        return items

    def extract_stream(self, server_url):
        """Resolve a server URL to a playable stream."""
        return base_extract_stream(server_url)