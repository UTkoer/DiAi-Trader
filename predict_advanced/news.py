from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urlsplit
from .data import parse_timestamp


def load_news(path):
    if not path:
        return [], "disabled"
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
        items = [json.loads(line) for line in text.splitlines() if line.strip()] if str(path).endswith(".jsonl") else json.loads(text)
        if not isinstance(items, list):
            raise ValueError("expected_news_array")
        return items, "loaded"
    except (OSError, ValueError):
        return [], "unavailable"


def filter_news(items, cutoff, max_results=5, index_code=None, max_chars=800):
    accepted, audit, candidates = [], [], []
    seen_urls, seen_titles, seen_events = set(), [], set()
    for item in items:
        record = {"accepted": False, "timestamp_valid": False}
        audit.append(record)
        if not isinstance(item, dict):
            record["reason"] = "invalid_record"
            continue
        record.update({key: item.get(key) for key in ("title", "url", "source", "published_at", "fetched_at")})
        try:
            published = parse_timestamp(item.get("published_at"))
            fetched = parse_timestamp(item.get("fetched_at"))
            record.update(published_at=published.isoformat(), fetched_at=fetched.isoformat())
            if published > cutoff:
                raise ValueError("published_after_cutoff")
            if fetched > cutoff:
                raise ValueError("snapshot_after_cutoff")
            if fetched < published:
                raise ValueError("snapshot_before_publication")
            if item.get("updated_at"):
                updated = parse_timestamp(item["updated_at"])
                if not published <= updated <= fetched:
                    raise ValueError("invalid_revision_time")
        except (TypeError, ValueError) as exc:
            record["reason"] = str(exc) if str(exc) in {
                "published_after_cutoff", "snapshot_after_cutoff", "snapshot_before_publication", "invalid_revision_time"
            } else "missing_or_invalid_timestamp"
            record["timestamp_filter_reason"] = record["reason"]
            continue
        record.update(timestamp_valid=True, timestamp_filter_reason="within_cutoff")
        if index_code is not None and (not isinstance(item.get("index_codes"), list) or index_code not in item["index_codes"]):
            record["reason"] = "unverified_index_relevance"
            continue
        try:
            url = urlsplit(item.get("url", ""))
            if url.scheme not in ("https", "http") or not url.netloc or url.username or url.password:
                raise ValueError("invalid_url")
            if any(not isinstance(item.get(key), str) or not item[key].strip() for key in ("title", "content", "source")):
                raise ValueError("missing_content")
        except (ValueError, TypeError):
            record["reason"] = "invalid_content_or_url"
            continue
        candidates.append((published, item, record))
    for published, item, record in sorted(candidates, key=lambda entry: entry[0], reverse=True):
        url = item["url"].split("#")[0].rstrip("/")
        title = re.sub(r"\W", "", item["title"].casefold())
        event_id = str(item.get("event_id", ""))
        if url in seen_urls or any(SequenceMatcher(None, title, seen).ratio() >= .9 for seen in seen_titles) or (event_id and event_id in seen_events):
            record["reason"] = "duplicate_event"
            continue
        seen_urls.add(url)
        seen_titles.append(title)
        if event_id:
            seen_events.add(event_id)
        if len(accepted) >= max_results:
            record["reason"] = "news_limit"
            continue
        record.update(accepted=True, reason="within_cutoff")
        accepted.append({"title": item["title"][:200], "url": item["url"], "source": item["source"][:200],
                         "published_at": record["published_at"], "fetched_at": record["fetched_at"],
                         "content": item["content"][:max_chars], "event_type": "unknown",
                         "source_quality": "unverified", "timestamp_valid": True})
    return accepted, audit
