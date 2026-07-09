# -*- coding: utf-8 -*-
"""
UI helper functions: text wrapping, cleaning titles, etc.
"""
import re

SAFE_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

def normalize_query(text):
    text = (text or "").strip().lower()
    text = text.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا").replace("ى", "ي")
    return "".join(ch for ch in text if ch.isalnum())

def strip_arabic_from_english_title(title):
    if not title:
        return title
    stripped = title.replace(" ", "")
    if not stripped:
        return title
    ar_count = sum(1 for c in stripped if "\u0600" <= c <= "\u06ff")
    if ar_count / len(stripped) >= 0.30:
        return title
    cleaned = re.sub(r"[\u0600-\u06ff]+", " ", title)
    cleaned = re.sub(r"[\s|\-–_]+$", "", cleaned)
    cleaned = re.sub(r"^[\s|\-–_]+", "", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" -|_")
    return cleaned if cleaned.strip() else title

def wrap_ui_text(text, width=40, max_lines=2, fallback=""):
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return fallback
    words = text.split(" ")
    lines = []
    current = ""
    for word in words:
        candidate = word if not current else "{} {}".format(current, word)
        if len(candidate) <= width:
            current = candidate
            continue
        if current:
            lines.append(current)
            if len(lines) >= max_lines:
                break
        current = word
    if len(lines) < max_lines and current:
        lines.append(current)
    if not lines:
        lines = [text[:width]]
    consumed = " ".join(lines)
    if len(consumed) < len(text):
        lines[-1] = lines[-1].rstrip(" .،") + "..."
    return "\n".join(lines[:max_lines])

def single_line_text(text, width=54, fallback=""):
    return wrap_ui_text(text, width=width, max_lines=1, fallback=fallback)