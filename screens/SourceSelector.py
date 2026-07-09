# -*- coding: utf-8 -*-
from Screens.Screen import Screen
from Components.ActionMap import ActionMap
from Components.MenuList import MenuList
from Components.Label import Label
from ..components.scrapers import SCRAPERS
from .MainScreen import MainScreen

class SourceSelector(Screen):
    skin = """
        <screen name="SourceSelector" position="center,center" size="800,600" title="اختر المصدر">
            <widget name="list" position="20,20" size="760,500" scrollbarMode="showOnDemand" />
            <widget name="status" position="20,530" size="760,40" font="Regular;24" halign="center" />
        </screen>
    """

    def __init__(self, session):
        Screen.__init__(self, session)
        self.session = session
        self["list"] = MenuList([])
        self["status"] = Label("اختر أحد المصادر")
        self["actions"] = ActionMap(["OkCancelActions"], {
            "ok": self.select,
            "cancel": self.close
        }, -1)
        self.onLayoutFinish.append(self.populate)

    def populate(self):
        items = [(name, scraper_class) for name, scraper_class in SCRAPERS.items()]
        self["list"].setList([name for name, _ in items])
        self._scrapers = items

    def select(self):
        idx = self["list"].getSelectedIndex()
        if idx < 0:
            return
        name, scraper_class = self._scrapers[idx]
        scraper = scraper_class()
        self.session.open(MainScreen, scraper=scraper, source_name=name)