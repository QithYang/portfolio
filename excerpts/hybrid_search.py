# Excerpt from the private Pink Anchor repository.
# Trimmed for readability; helper functions and imports are omitted.
#
# Hybrid recall: SQLite FTS5 (BM25) + embedding similarity, merged and re-scored.

import math
from datetime import datetime, timezone

TIME_DECAY_TAU = 90        # days
RECALL_SCORE_THRESHOLD = 0.25   # drop weak matches
VECTOR_THRESHOLD = 0.38         # used by the tool-facing search (0.45 / 0.40 / 0.38 by recall mode)


# ---------------------------------------------------------------- scoring

def compute_similarity(bm25_raw: float) -> float:
    """Monotonic compression into [0, 1): s = b / (1 + b).
    SQLite FTS5 bm25() returns NEGATIVE scores (more negative = more relevant)."""
    b = abs(bm25_raw)
    return b / (1.0 + b)


def compute_time_weight(created_at: str, category: str = "") -> float:
    """Category-aware decay. Facts never decay; beliefs/relationships decay slowly
    (time constant 180 days); events/decisions decay normally (90 days).
    Floor of 0.5 so old memories are down-weighted, never erased."""
    if category == "fact":
        return 1.0
    try:
        created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        days = max(0, (datetime.now(timezone.utc) - created).total_seconds() / 86400.0)
    except (ValueError, TypeError):
        days = 0
    tau = TIME_DECAY_TAU * 2 if category in ("belief", "relationship") else TIME_DECAY_TAU
    return 0.5 + 0.5 * math.exp(-days / tau)


def compute_score(mem: dict, query_keywords: list) -> float:
    score = compute_similarity(mem.get("bm25_raw", 0)) * compute_time_weight(
        mem.get("created_at", ""), mem.get("category", ""))

    # Keyword overlap: proportional bonus (0..0.5), not all-or-nothing.
    mem_kw = {k.strip() for k in (mem.get("keywords") or "").split(",") if k.strip()}
    if query_keywords and mem_kw:
        hits = sum(1 for qk in query_keywords if qk in mem_kw)
        score += 0.5 * hits / len(query_keywords)

    if mem.get("tag") and mem["tag"] in query_keywords:   # exact tag match
        score += 0.2
    if mem.get("category") == "fact":                     # facts preferred on lookup
        score += 0.15
    imp = mem.get("importance", 2)                         # small tie-breaker only
    score += (0.25 if imp >= 4 else 0.1 if imp >= 3 else 0) + (0.5 if mem.get("is_pinned") else 0)
    return score


# ---------------------------------------------------------------- search pipeline

def search_memories(query: str, limit: int = 10, category: str | None = None) -> list[dict]:
    """FTS5 -> LIKE fallback -> vector search -> merge -> rerank -> threshold."""
    query_keywords = extract_recall_keywords(query)   # jieba-based keyword extraction
    fetch_limit = max(limit * 2, 15)

    # 1) Full-text path. fts_search() tries an AND query first, then a looser OR query.
    # safe_call(): run, log any exception as a warning, return None.
    raw_results = safe_call(fts_search, query, limit=fetch_limit) or []
    if not raw_results:   # plain LIKE fallback
        raw_results = safe_call(keyword_search, query, limit=fetch_limit) or []

    # IMPORTANT: no early return here. A query made only of function words
    # (e.g. "do you remember?") produces zero FTS tokens, so the keyword paths
    # return nothing. Such queries depend entirely on the vector path below.

    # 2) Vector path (skipped silently if embeddings are unavailable -> FTS-only).
    vector_sim = {}
    if HAS_VECTOR and embedding_cache:
        try:
            for vr in run_vector_search(query, limit=fetch_limit, threshold=VECTOR_THRESHOLD):
                vector_sim[vr["id"]] = vr["vector_sim"]

            # Pull in rows that only the vector path found, with a pseudo-BM25 score
            # so they can be ranked on the same scale.
            fts_ids = {r["id"] for r in raw_results}
            vector_only = [i for i in vector_sim if i not in fts_ids]
            if vector_only:
                placeholders = ",".join("?" * len(vector_only))
                rows = execute_query(
                    f"""SELECT id, what, keywords, category, tag, importance,
                               created_at, is_pinned
                        FROM memories
                        WHERE id IN ({placeholders}) AND deleted_at IS NULL""",
                    vector_only,
                )
                for row in rows:
                    row["bm25_raw"] = -(vector_sim.get(row["id"], 0.5) * 8)
                    row["_vector_only"] = True
                    raw_results.append(row)
        except Exception as e:
            logger.warning(f"vector search error, continuing FTS-only: {e}")

    # 3) Re-rank on the combined candidate set.
    for mem in raw_results:
        mem["final_score"] = compute_score(mem, query_keywords)

    # 4) Non-linear vector bonus + dual-path bonus.
    for mem in raw_results:
        vsim = vector_sim.get(mem["id"])
        if vsim is None:
            continue
        # Near-zero for weak similarity, grows quickly for strong similarity.
        mem["final_score"] += 0.8 * max(0, vsim - 0.35) ** 1.5
        # Found by BOTH keyword and semantic search -> most trustworthy.
        if not mem.get("_vector_only"):
            mem["final_score"] += 0.15

    ranked = sorted(raw_results, key=lambda m: m["final_score"], reverse=True)
    return [m for m in ranked if m["final_score"] >= RECALL_SCORE_THRESHOLD
            and (not category or m.get("category") == category)][:limit]
