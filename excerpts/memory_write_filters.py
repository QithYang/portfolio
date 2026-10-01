# Excerpt from the private Pink Anchor repository.
# Trimmed for readability.
#
# Part 1: content filters applied to candidate memories proposed by the LLM
#         extraction step (the "what" field is the one-sentence memory text).
# Part 2: the unified write entry point used by every client (tool calls, API,
#         extraction): rate limit -> 24h same-tag dedup -> merge_key merge ->
#         fallback dedup -> insert (+ async embedding).
# Filter patterns were originally Chinese; shown here as English equivalents.

import re
from difflib import SequenceMatcher
HOURLY_LIMIT = 10
DAILY_LIMIT = 30

# === Part 1: content filters ===
# Filter 1: meta-discussion. Conversations ABOUT the assistant's memory, tokens,
# prompts or models are flagged so only real-life facts in them get stored.
META_INPUT_PATTERNS = [
    r"(memory|remember).{0,6}(system|architecture|write|recall|limit|quality)",
    r"token.{0,4}(budget|limit|usage)",
    r"(model).{0,4}(switch|change to)",
    r"(edit|rewrite|tune).{0,6}prompt",
]

# Filter 2: system-topic keywords. A candidate mentioning the service's own
# internals is about the tool, not about the user's life -> rejected.
SYSTEM_META_KEYWORDS = ["memory system", "recall mechanism", "docker", "container",
    "config", "fts5", "embedding", "schema", "system prompt", "context window",
    "token budget", "bug fix", "model switch"]

# Filter 3: abstract language. "what" must be concrete, not an analyst's report.
ABSTRACT_WORDS = ["led to", "reflects", "indicates", "demonstrates", "implies",
                  "showed curiosity", "towards the AI", "towards the system"]
MIN_WHAT_CHARS = 15   # Filter 4: too short = no information


def is_meta_input(text: str) -> bool:
    return any(re.search(p, text, re.IGNORECASE) for p in META_INPUT_PATTERNS)


def is_system_meta(what: str, keywords: str) -> bool:
    return any(kw in f"{what} {keywords}".lower() for kw in SYSTEM_META_KEYWORDS)


def validate_candidate(result: dict) -> str | None:
    """Return None if the candidate passes, else the rejection reason."""
    what = result.get("what", "")
    # (Required fields, category/tag enum checks, keyword count >= 3 and
    #  merge_key format "<tag>_<core word>" are checked before this point.)
    hits = [w for w in ABSTRACT_WORDS if w in what]
    if hits:
        return f"abstract wording ({','.join(hits)}); make it concrete"
    if len(re.sub(r"[\s,.!?]", "", what)) < MIN_WHAT_CHARS:
        return f"'what' shorter than {MIN_WHAT_CHARS} characters"
    return None


# === Part 2: write pipeline ===
def _check_throttle(importance: int) -> tuple[bool, str]:
    if get_daily_memory_count() >= DAILY_LIMIT:
        return False, f"daily limit ({DAILY_LIMIT}) reached"
    if get_hourly_memory_count() >= HOURLY_LIMIT and importance < 3:   # important items may pass
        return False, f"hourly limit ({HOURLY_LIMIT}) reached"
    return True, "OK"


def _pre_write_dedup(memory: dict) -> tuple[str, int | None]:
    """Compare with memories of the same tag from the last 24 hours."""
    rows = execute_query(
        """SELECT id, what FROM memories
           WHERE deleted_at IS NULL AND tag = ?
             AND created_at > datetime('now', '-24 hours')
           ORDER BY created_at DESC LIMIT 10""",
        (memory["tag"],),
    )
    for row in rows:
        sim = SequenceMatcher(None, memory["what"], row["what"] or "").ratio()
        if sim >= 0.8:
            return "skip", row["id"]       # near-identical
        if sim >= 0.5:
            return "merge", row["id"]      # same topic, refine existing row
    return "allow", None


def _find_merge_by_key(memory: dict) -> int | None:
    """merge_key is '<tag>_<core word>', e.g. 'habit_morning-run'.
    Same key = same subject, merged permanently (no time window)."""
    rows = execute_query("SELECT id FROM memories WHERE deleted_at IS NULL AND merge_key = ? "
                         "ORDER BY created_at DESC LIMIT 1", (memory["merge_key"],))
    return rows[0]["id"] if rows else None
    # (A fuzzier second path: sibling-tag keys via LIKE + character similarity
    #  >= 0.55. Blind spot: similar wording with opposite meaning gets merged.)


def write_memory(memory: dict) -> dict:
    """Returns {"status": "created" | "merged" | "skipped", "id": ..., "reason": ...}."""
    if not (memory.get("what") or "").strip():
        return {"status": "skipped", "id": None, "reason": "empty"}
    ok, reason = _check_throttle(memory.get("importance", 2))
    if not ok:  # rate-limited
        return {"status": "skipped", "id": None, "reason": reason}

    action, existing_id = _pre_write_dedup(memory)
    if action == "skip":
        return {"status": "skipped", "id": existing_id, "reason": "duplicate (24h same tag)"}
    if action == "merge" and merge_into(existing_id, memory):
        return {"status": "merged", "id": existing_id, "reason": "24h tag merge"}

    target = _find_merge_by_key(memory)
    if target and merge_into(target, memory):
        # merge_into(): newer text wins for beliefs/preferences ("latest is truth");
        # for facts/events the longer text is kept; keywords unioned (max 5);
        # importance = max(old, new); FTS row and embedding refreshed.
        return {"status": "merged", "id": target, "reason": "merge_key match"}

    # (Omitted: a last-resort dedup over the last 7 days at similarity >= 0.85.)
    new_id = insert_memory(memory)          # INSERT + FTS5 row (jieba-tokenised)
    schedule_embedding(new_id, memory)      # async; failure never blocks the write
    return {"status": "created", "id": new_id, "reason": "new memory"}
