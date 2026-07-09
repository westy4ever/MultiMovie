# -*- coding: utf-8 -*-
"""
Search ranking and suggestions from library/TMDb.
"""
from .ui import normalize_query
from .library import favorite_items, history_items
from .tmdb import tmdb_search_suggestions

_TYPE_LABELS = {"movie": "فيلم", "series": "مسلسل", "episode": "حلقة"}

def rank_search_items(items, query):
    q = normalize_query(query)
    q_words = [w for w in q.split() if len(w) >= 2] if q else []
    strong = []
    weak = []
    for item in items:
        title = item.get("title", "")
        ntitle = normalize_query(title)
        rank = 9
        if not q:
            rank = 5
        elif ntitle == q:
            rank = 0
        elif ntitle.startswith(q):
            rank = 1
        elif q in ntitle:
            rank = 2
        elif q_words:
            matched_words = sum(1 for w in q_words if w in ntitle)
            if matched_words == len(q_words):
                rank = 3
            elif matched_words >= max(1, len(q_words) * 2 // 3):
                rank = 4
            elif matched_words > 0:
                rank = 5
        entry = (rank, title.lower(), item)
        if rank <= 3:
            strong.append(entry)
        elif rank <= 5:
            weak.append(entry)
    strong.sort(key=lambda r: (r[0], r[1]))
    weak.sort(key=lambda r: (r[0], r[1]))
    result = [r[2] for r in strong]
    if len(result) < 3:
        result += [r[2] for r in weak[:max(0, 5 - len(result))]]
    if not result and weak:
        result = [r[2] for r in weak]
    return result

def library_search_suggestions(query="", current_site="", limit=8):
    """
    Suggest from favorites and history, sorted by relevance.
    """
    q = normalize_query(query)
    rows = []
    seen = set()
    for source_name, items, source_rank in (
        ("المفضلة", favorite_items(), 0),
        ("السجل", history_items(), 1),
    ):
        for item in items or []:
            title = item.get("title", "").strip()
            if not title:
                continue
            norm = normalize_query(title)
            if not norm or norm in seen:
                continue
            if q:
                if norm == q:
                    score = 0
                elif norm.startswith(q):
                    score = 1
                elif q in norm:
                    score = 2
                else:
                    continue
            else:
                score = 5
            if current_site and item.get("_site") == current_site:
                score -= 1
            seen.add(norm)
            rows.append((
                score,
                source_rank,
                -int(item.get("_saved_at") or 0),
                {
                    "title": title,
                    "query": title,
                    "source": source_name,
                    "site": item.get("_site", ""),
                    "kind": _TYPE_LABELS.get(item.get("type", ""), ""),
                    "year": item.get("year", ""),
                }
            ))
    rows.sort(key=lambda row: (row[0], row[1], row[2]))
    suggestions = [row[3] for row in rows[:limit]]
    # Add TMDb suggestions if fewer than limit
    if len(suggestions) < limit and query:
        tmdb_sugg = tmdb_search_suggestions(query, limit=limit - len(suggestions))
        suggestions.extend(tmdb_sugg)
    return suggestions[:limit]