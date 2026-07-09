# -*- coding: utf-8 -*-
from Screens.Screen import Screen
from Components.ActionMap import ActionMap
from Components.MenuList import MenuList
from Components.Label import Label
from Components.Pixmap import Pixmap
from enigma import ePicLoad, eTimer
import threading
import time
import os
from ..utils.base import log, extract_stream as base_extract_stream
from ..utils.poster import download_poster, get_cached_poster
from ..utils.tmdb import merge_tmdb_data, tmdb_enabled
from ..utils.library import toggle_favorite_entry, is_favorite, upsert_library_item, get_saved_position
from ..utils.ui import wrap_ui_text, single_line_text, strip_arabic_from_english_title
from ..utils.player import play
from .Player import MultiMoviePlayer

class MovieDetailsScreen(Screen):
    skin = """
    <screen name="MovieDetailsScreen" position="center,center" size="950,620" title="التفاصيل">
        <widget name="title" position="20,20" size="910,40" font="Regular;28" foregroundColor="#00E5FF" halign="center" />
        <widget name="poster" position="20,70" size="200,280" zPosition="1" alphatest="blend" />
        <widget name="plot" position="240,70" size="690,120" font="Regular;20" />
        <widget name="info" position="240,200" size="690,60" font="Regular;18" foregroundColor="#8B949E" />
        <widget name="servers" position="20,370" size="910,200" scrollbarMode="showOnDemand" />
        <widget name="status" position="20,580" size="910,30" font="Regular;22" halign="center" />
        <widget name="key_red" position="20,590" size="180,30" font="Regular;20" foregroundColor="#FF6B6B" />
        <widget name="key_yellow" position="720,590" size="180,30" font="Regular;20" foregroundColor="#FFD740" halign="right" />
    </screen>
    """

    def __init__(self, session, scraper, movie):
        Screen.__init__(self, session)
        self.session = session
        self.scraper = scraper
        self.movie = movie
        self.details = None
        self.servers = []
        self.episodes = []
        self.picload = ePicLoad()
        self.picload.PictureData.get().append(self._paint_poster)
        self._closed = False

        self["title"] = Label(movie.get("title", "بدون عنوان"))
        self["poster"] = Pixmap()
        self["plot"] = Label("جاري تحميل التفاصيل...")
        self["info"] = Label("")
        self["servers"] = MenuList([])
        self["status"] = Label("")
        self["key_red"] = Label("المفضلة")
        self["key_yellow"] = Label("تحديث TMDb")

        self["actions"] = ActionMap(["OkCancelActions", "DirectionActions", "ColorActions"], {
            "ok": self._on_ok,
            "cancel": self._on_cancel,
            "red": self._toggle_favorite,
            "yellow": self._refresh_tmdb,
            "up": lambda: self["servers"].up(),
            "down": lambda: self["servers"].down(),
        }, -1)

        self.onLayoutFinish.append(self._load)

    def _load(self):
        threading.Thread(target=self._bg_load, daemon=True).start()

    def _bg_load(self):
        url = self.movie.get("url")
        if not url:
            url = self.movie.get("id")  # some scrapers pass id
        details = self.scraper.get_details(url)
        if details:
            details = merge_tmdb_data(details)
        callInMainThread(self._on_loaded, details)

    def _on_loaded(self, details):
        if self._closed:
            return
        if not details:
            self["plot"].setText("تعذر تحميل التفاصيل")
            return
        self.details = details
        title = details.get("title", "بدون عنوان")
        self["title"].setText(wrap_ui_text(title, width=30, max_lines=2))
        plot = details.get("plot") or "لا توجد قصة"
        if len(plot) > 300:
            plot = plot[:300] + "..."
        self["plot"].setText(plot)

        info_parts = []
        if details.get("year"):
            info_parts.append(f"السنة: {details['year']}")
        if details.get("rating"):
            info_parts.append(f"التقييم: {details['rating']}/10")
        if details.get("genres"):
            info_parts.append(f"النوع: {details['genres']}")
        self["info"].setText(" | ".join(info_parts))

        # servers and episodes
        self.servers = details.get("servers", [])
        self.episodes = [e for e in details.get("items", []) if e.get("type") == "episode"]

        # decide what to display
        display_list = []
        if self.episodes:
            for ep in self.episodes:
                display_list.append(ep.get("title", "حلقة"))
            self["servers"].setList(display_list)
            self["status"].setText(f"{len(self.episodes)} حلقة")
        elif self.servers:
            for s in self.servers:
                display_list.append(s.get("name", "سيرفر") + (f" [{s.get('quality','')}]" if s.get('quality') else ""))
            self["servers"].setList(display_list)
            self["status"].setText(f"{len(self.servers)} سيرفر")
        else:
            self["servers"].setList(["لا توجد سيرفرات أو حلقات"])
            self["status"].setText("لا توجد سيرفرات")

        # poster
        poster_url = details.get("poster") or self.movie.get("poster")
        if poster_url:
            threading.Thread(target=self._load_poster, args=(poster_url,), daemon=True).start()

        # update favorite button
        fav = is_favorite(self.movie.get("url", ""))
        self["key_red"].setText("المفضلة" if not fav else "إزالة من المفضلة")

    def _load_poster(self, url):
        cached = get_cached_poster(url)
        if cached:
            callInMainThread(self._display_poster, cached)
            return
        local_path = download_poster(url)
        if local_path:
            callInMainThread(self._display_poster, local_path)

    def _display_poster(self, path):
        try:
            self.picload.setPara((self["poster"].instance.size().width(), self["poster"].instance.size().height(), 1, 1, 0, 1, "#000000"))
            self.picload.startDecode(path)
        except Exception as e:
            log("_display_poster error: {}".format(e))

    def _paint_poster(self, picData=None):
        ptr = self.picload.getData()
        if ptr:
            self["poster"].instance.setPixmap(ptr)
            self["poster"].show()

    def _on_ok(self):
        idx = self["servers"].getSelectedIndex()
        if idx < 0:
            return
        if self.episodes:
            if idx < len(self.episodes):
                ep = self.episodes[idx]
                # open new details for episode
                self.session.open(MovieDetailsScreen, self.scraper, ep)
            return
        if self.servers and idx < len(self.servers):
            server = self.servers[idx]
            self["status"].setText("جاري استخراج الرابط...")
            threading.Thread(target=self._bg_extract, args=(server,), daemon=True).start()

    def _bg_extract(self, server):
        # use the scraper's extract_stream if available, else base extractor
        stream_url, quality, ref = None, None, None
        if hasattr(self.scraper, "extract_stream"):
            stream_url, quality, ref = self.scraper.extract_stream(server["url"])
        else:
            stream_url, quality, ref = base_extract_stream(server["url"])
        callInMainThread(self._on_stream_ready, stream_url, quality, ref, server)

    def _on_stream_ready(self, stream_url, quality, ref, server):
        if self._closed:
            return
        if not stream_url:
            self["status"].setText("فشل استخراج الرابط - جرب سيرفر آخر")
            return
        # save to history
        item = dict(self.movie)
        item.update(self.details or {})
        entry = {
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "poster": item.get("poster", ""),
            "plot": item.get("plot", ""),
            "year": item.get("year", ""),
            "rating": item.get("rating", ""),
            "type": item.get("type", "movie"),
            "_site": getattr(self.scraper, "base_url", ""),
            "_m_type": item.get("type", "movie"),
            "_saved_at": int(time.time()),
            "server_name": server.get("name", ""),
            "quality": quality or "",
            "last_stream_url": stream_url,
        }
        upsert_library_item("history", entry, limit=120)

        # play
        title = self["title"].getText()
        item_url = self.movie.get("url", "")
        saved_pos = get_saved_position(item_url)
        play(self.session, stream_url, title, resume_pos=saved_pos, item_url=item_url)
        self.close()

    def _toggle_favorite(self):
        if not self.details:
            return
        item = dict(self.movie)
        item.update(self.details)
        entry = {
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "poster": item.get("poster", ""),
            "plot": item.get("plot", ""),
            "year": item.get("year", ""),
            "rating": item.get("rating", ""),
            "type": item.get("type", "movie"),
            "_site": getattr(self.scraper, "base_url", ""),
            "_m_type": item.get("type", "movie"),
            "_saved_at": int(time.time()),
        }
        added = toggle_favorite_entry(entry)
        self["key_red"].setText("المفضلة" if not added else "إزالة من المفضلة")
        self["status"].setText("تمت الإضافة" if added else "تم الحذف")

    def _refresh_tmdb(self):
        if not self.details or not tmdb_enabled():
            self["status"].setText("فعّل TMDb في الإعدادات أولاً")
            return
        self["status"].setText("جاري تحديث من TMDb...")
        threading.Thread(target=self._bg_refresh_tmdb, daemon=True).start()

    def _bg_refresh_tmdb(self):
        merged = merge_tmdb_data(self.details)
        callInMainThread(self._on_loaded, merged)

    def _on_cancel(self):
        self._closed = True
        self.close()