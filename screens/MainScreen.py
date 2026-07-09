# -*- coding: utf-8 -*-
from Screens.Screen import Screen
from Components.ActionMap import ActionMap
from Components.MenuList import MenuList
from Components.Label import Label
from .MovieGridScreen import MovieGridScreen
from ..utils.library import favorite_items, history_items

class MainScreen(Screen):
    skin = """
        <screen name="MainScreen" position="center,center" size="800,600" title="الأقسام">
            <widget name="title" position="20,20" size="760,40" font="Regular;28" foregroundColor="#00E5FF" halign="center" />
            <widget name="list" position="20,70" size="760,460" scrollbarMode="showOnDemand" />
            <widget name="status" position="20,540" size="760,40" font="Regular;22" halign="center" />
            <widget name="key_red" position="20,560" size="180,30" font="Regular;20" foregroundColor="#FF6B6B" />
            <widget name="key_green" position="220,560" size="180,30" font="Regular;20" foregroundColor="#39D98A" />
            <widget name="key_yellow" position="440,560" size="180,30" font="Regular;20" foregroundColor="#FFD740" />
            <widget name="key_blue" position="660,560" size="180,30" font="Regular;20" foregroundColor="#58A6FF" halign="right" />
        </screen>
    """

    def __init__(self, session, scraper, source_name):
        Screen.__init__(self, session)
        self.session = session
        self.scraper = scraper
        self.source_name = source_name
        self._categories = []
        self["title"] = Label(source_name)
        self["list"] = MenuList([])
        self["status"] = Label("")
        self["key_red"] = Label("بحث")
        self["key_green"] = Label("المفضلة")
        self["key_yellow"] = Label("السجل")
        self["key_blue"] = Label("")
        self["actions"] = ActionMap(["OkCancelActions", "ColorActions"], {
            "ok": self.select_category,
            "cancel": self.close,
            "red": self.do_search,
            "green": self.show_favorites,
            "yellow": self.show_history,
        }, -1)
        self.onLayoutFinish.append(self.load_categories)

    def load_categories(self):
        self["status"].setText("جاري تحميل الأقسام...")
        cats = self.scraper.get_categories()
        if not cats:
            self["list"].setList(["لا توجد أقسام"])
            self["status"].setText("فشل تحميل الأقسام")
            return
        self._categories = cats
        self["list"].setList([c[0] for c in cats])
        self["status"].setText(f"{len(cats)} قسم")

    def select_category(self):
        idx = self["list"].getSelectedIndex()
        if idx < 0 or not self._categories:
            return
        cat_name, cat_slug = self._categories[idx]
        self.session.open(MovieGridScreen, scraper=self.scraper, category=cat_slug, title=cat_name)

    def do_search(self):
        from Screens.VirtualKeyBoard import VirtualKeyBoard
        self.session.openWithCallback(self.on_search_query, VirtualKeyBoard, title="ابحث عن فيلم أو مسلسل")

    def on_search_query(self, query):
        if not query:
            return
        self.session.open(MovieGridScreen, scraper=self.scraper, category="search", title=f"بحث: {query}", search_query=query)

    def show_favorites(self):
        favs = favorite_items()
        if not favs:
            self["status"].setText("لا توجد مفضلات")
            return
        self.session.open(MovieGridScreen, scraper=self.scraper, category="favorites", title="المفضلة", items_override=favs)

    def show_history(self):
        hist = history_items()
        if not hist:
            self["status"].setText("لا توجد عناصر في السجل")
            return
        self.session.open(MovieGridScreen, scraper=self.scraper, category="history", title="السجل", items_override=hist)