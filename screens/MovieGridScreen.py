# -*- coding: utf-8 -*-
import threading
from Screens.Screen import Screen
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.MenuList import MenuList
from .MovieDetailsScreen import MovieDetailsScreen
from ..utils.base import log

class MovieGridScreen(Screen):
    skin = """
        <screen name="MovieGridScreen" position="center,center" size="950,620" title="الأفلام">
            <widget name="title" position="20,20" size="910,40" font="Regular;28" foregroundColor="#00E5FF" halign="center" />
            <widget name="grid" position="20,70" size="910,470" scrollbarMode="showOnDemand" />
            <widget name="status" position="20,550" size="910,30" font="Regular;22" halign="center" />
            <widget name="pagination" position="20,590" size="910,30" font="Regular;20" halign="center" />
            <widget name="key_red" position="20,600" size="180,30" font="Regular;20" foregroundColor="#FF6B6B" />
            <widget name="key_green" position="220,600" size="180,30" font="Regular;20" foregroundColor="#39D98A" />
            <widget name="key_yellow" position="440,600" size="180,30" font="Regular;20" foregroundColor="#FFD740" />
        </screen>
    """

    def __init__(self, session, scraper, category, title, search_query=None, items_override=None):
        Screen.__init__(self, session)
        self.session = session
        self.scraper = scraper
        self.category = category
        self.title = title
        self.search_query = search_query
        self.items_override = items_override
        self.page = 1
        self.movies = []

        self["title"] = Label(title)
        self["grid"] = MenuList([])
        self["status"] = Label("جاري التحميل...")
        self["pagination"] = Label("الصفحة 1")
        self["key_red"] = Label("فلتر")
        self["key_green"] = Label("التالي")
        self["key_yellow"] = Label("السابق")

        self["actions"] = ActionMap(["OkCancelActions", "ColorActions", "DirectionActions"], {
            "ok": self._open_details,
            "cancel": self.close,
            "red": self._filter_menu,
            "green": self._next_page,
            "yellow": self._prev_page,
            "up": lambda: self["grid"].up(),
            "down": lambda: self["grid"].down(),
            "left": lambda: self["grid"].pageUp(),
            "right": lambda: self["grid"].pageDown(),
        }, -1)

        self.filters = {}
        self.onLayoutFinish.append(self._load)

    def _load(self):
        if self.items_override is not None:
            self.movies = self.items_override
            self._display_movies()
            self["pagination"].setText("")
            self["status"].setText(f"{len(self.movies)} عنصر")
        else:
            self._fetch_page(1)

    def _fetch_page(self, page=1):
        self.page = page
        threading.Thread(target=self._bg_fetch, daemon=True).start()

    def _bg_fetch(self):
        try:
            if self.search_query:
                items = self.scraper.search(self.search_query, page=self.page)
            else:
                items = self.scraper.get_movies(self.category, page=self.page, filters=self.filters)
            # Update UI in main thread
            try:
                from enigma import eTimer
                timer = eTimer()
                timer.callback.append(lambda: self._on_fetch_done(items))
                timer.start(50, True)
            except:
                self._on_fetch_done(items)
        except Exception as e:
            log(f"MovieGridScreen fetch error: {e}")
            self._on_fetch_done([])

    def _on_fetch_done(self, items):
        if items is None:
            items = []
        self.movies = items
        self._display_movies()
        self["pagination"].setText(f"الصفحة {self.page}")
        self["status"].setText(f"{len(self.movies)} نتيجة - صفحة {self.page}")

    def _display_movies(self):
        if not self.movies:
            self["grid"].setList(["لا توجد نتائج"])
            self["status"].setText("لا توجد نتائج")
            return
        display = []
        for movie in self.movies:
            title = movie.get("title", "بدون عنوان")
            if movie.get("quality"):
                title += f" [{movie['quality']}]"
            if movie.get("rating"):
                title += f" ★{movie['rating']}"
            display.append(title)
        self["grid"].setList(display)

    def _open_details(self):
        idx = self["grid"].getSelectedIndex()
        if idx < 0 or idx >= len(self.movies):
            return
        movie = self.movies[idx]
        self.session.open(MovieDetailsScreen, self.scraper, movie)

    def _filter_menu(self):
        from Screens.VirtualKeyBoard import VirtualKeyBoard
        self.session.openWithCallback(self._on_filter_input, VirtualKeyBoard, title="أدخل الفلتر (مثال: genre=action)")

    def _on_filter_input(self, text):
        if text:
            try:
                key, val = text.split("=", 1)
                self.filters[key.strip()] = val.strip()
            except:
                pass
        self._fetch_page(1)

    def _next_page(self):
        if self.items_override is not None:
            return
        self._fetch_page(self.page + 1)

    def _prev_page(self):
        if self.items_override is not None or self.page <= 1:
            return
        self._fetch_page(self.page - 1)