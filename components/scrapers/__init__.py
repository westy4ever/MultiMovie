# -*- coding: utf-8 -*-
from .topcinema import TopCinemaScraper
from .egydead import EgyDeadScraper
from .wecima import WecimaScraper
from .arabseed import ArabseedScraper
from .shaheed import ShaheedScraper
from .faselhd_rip import FaselHDRipScraper
from .faselhd_hdx import FaselHDHdxScraper

SCRAPERS = {
    "TopCinema": TopCinemaScraper,
    "EgyDead": EgyDeadScraper,
    "WeCima": WecimaScraper,
    "Arabseed": ArabseedScraper,
    "Shaheed": ShaheedScraper,
    "FaselHD (RIP)": FaselHDRipScraper,
    "FaselHD (HDX)": FaselHDHdxScraper,
}