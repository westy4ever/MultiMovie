# -*- coding: utf-8 -*-
class BaseScraper:
    """Abstract base for all site scrapers."""

    def get_categories(self):
        """Return list of (display_name, slug)"""
        return []

    def get_movies(self, category, page=1, filters=None):
        """Return list of movie dicts with at least: title, id/url, poster, quality, rating, type."""
        return []

    def get_details(self, item_id_or_url):
        """Return dict with: title, plot, year, rating, genres, poster, servers (list of {name, url, quality}), items (episodes)."""
        return {}

    def search(self, query, page=1):
        """Return list of movie dicts from search."""
        return []

    # optionally implement extract_stream if the site needs custom extraction
    # otherwise the base.py generic resolver will be used