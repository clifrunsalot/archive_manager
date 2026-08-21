"""Small local lexical retriever used alongside dense Qdrant results."""

import re
from pathlib import Path
from typing import Any


def lexical_search(
    question: str,
    cache: dict[str, str],
    searchable_dir: Path,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Return sidecar candidates ranked by exact token overlap."""
    tokens = set(re.findall(r"[a-z0-9]{2,}", question.lower()))
    candidates = []
    for doc_id, source in cache.items():
        try:
            text = (searchable_dir / f"{doc_id}.txt").read_text(encoding="utf-8")
        except OSError:
            continue
        text_tokens = set(re.findall(r"[a-z0-9]{2,}", text.lower()))
        score = len(tokens & text_tokens)
        if score:
            candidates.append(
                {
                    "id": f"lexical:{doc_id}",
                    "score": score,
                    "payload": {
                        "doc_id": doc_id,
                        "source": source,
                        "page": 1,
                        "chunk_index": 0,
                        "text": text[:2500],
                    },
                }
            )
    candidates.sort(key=lambda hit: hit["score"], reverse=True)
    return candidates[:limit]