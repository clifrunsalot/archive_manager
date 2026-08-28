"""Query orchestration shared by HTTP and future UI clients."""

from __future__ import annotations

from archive_manager.retrieval import query as query_runtime
from archive_manager.security.access_policy import as_user


class QueryService:
    """Run an archive query with an explicit request-scoped identity."""

    def answer(self, question: str, user: str, top_k: int = 10, max_excerpt_chars: int = 1200):
        with as_user(user):
            return query_runtime.answer(
                question,
                top_k=top_k,
                max_excerpt_chars=max_excerpt_chars,
            )