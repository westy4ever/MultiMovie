# -*- coding: utf-8 -*-
from .base_scraper import BaseScraper

class EgyDeadScraper(BaseScraper):
    def __init__(self):
        self.base_url = "https://egydead.tv"

    # implement similar methods...