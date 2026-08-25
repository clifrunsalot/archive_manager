# Migration to RAG-First Architecture

## Goal

Shift this repository from a parser-first, query-intent-heavy architecture to a RAG-first architecture, while preserving deterministic enforcement for authorization, event scoping, and a small set of exact utility queries.

## 1. Define the target architecture boundary

Make `query.py` the primary orchestration layer for all non-trivial questions, with deterministic routing used only for:

- authorization and visibility filtering
- filename, event, and source inventory lookups
- broad-scope clarification
- exact numeric or reporting queries that must remain non-LLM

This fits the current structure because `answer()` already separates planning, deterministic routing, retrieval, and response generation in `src/archive_manager/retrieval/query.py:1407-1592`, and the current deterministic handler registry is isolated in `src/archive_manager/retrieval/query.py:1204-1354` and `src/archive_manager/retrieval/query_handlers.py:10-25`.

## 2. Shrink the query planner from intent catalog to routing policy

Refactor the planner in `src/archive_manager/retrieval/query_planner.py:8-181` so it no longer tries to classify many business-specific intents such as `performed_services`, `repair_cause_inventory`, `label_values_inventory`, and similar service-record-specific variants.

Replace that large intent taxonomy with a smaller routing model such as:

- deterministic utility query
- constrained exact query
- general RAG query
- broad query requiring narrowing
- multi-document summary query

This removes the need to keep expanding regex rules as new record types are added. It also decouples future document families from planner maintenance. The current evaluation harness is tightly coupled to exact expected intents in `src/archive_manager/evaluation/evaluation.py:19-34`, so that evaluation model should also be simplified during this phase.

## 3. Recast deterministic parsing as optional enrichment, not the main answer path

Today, automotive and domain parsers are central to many answers:

- automotive parser: `src/archive_manager/domain/automotive_parser.py:41-280`
- domain parser registry: `src/archive_manager/domain/domain_parsers.py:71-111`
- ingest-time EventFacts extraction: `src/archive_manager/core/event_facts.py:20-64`
- EventFacts persistence and use: `src/archive_manager/core/event_facts.py:67-84`, `src/archive_manager/retrieval/query.py:268-320`, and `src/archive_manager/retrieval/query.py:494-520`

Keep these components, but change their role:

- use them to enrich retrieval metadata
- use them to validate or annotate answers when needed
- stop relying on them as the default way to answer content questions

This is the key maintenance win: new document types should still be usable through RAG even before any parser exists.

## 4. Make ingestion produce better retrieval assets instead of parser-specific caches

The current ingestion pipeline already has the right backbone:

- normalization, OCR, text extraction, chunking, embedding, and Qdrant upsert in `src/archive_manager/ingestion/ingest.py:533-758`
- OCR adapter boundary in `src/archive_manager/ingestion/ocr_adapters.py:20-122`

The migration should focus ingestion on retrieval quality:

- improve chunk metadata stored with each Qdrant point
- preserve event, document, and page provenance
- attach normalized manifest and domain fields when available
- prepare for layout-aware retrieval using the existing `.ocr.json` sidecar contract referenced in `src/archive_manager/ingestion/ocr_adapters.py:28`, `79`, and `103-104`

In practice, the chunk payload created in `src/archive_manager/ingestion/ingest.py:694-718` should become the primary long-term contract, and file-based caches should become secondary implementation details.

## 5. Replace hybrid retrieval as a special case with retrieval as the default

Right now, only free-text queries clearly use hybrid retrieval:

- dense retrieval from Qdrant via `qdrant_search()` in `src/archive_manager/ingestion/ingest.py:398-412`
- lexical retrieval via `lexical_search()` in `src/archive_manager/retrieval/hybrid_retrieval.py:8-39`
- retrieval merge and filtering in `src/archive_manager/retrieval/query.py:1450-1479`

Make this retrieval stack the default for most user questions. Extend it so that:

- manifest-backed events can be retrieved as grouped records, not just isolated chunks
- event and page ordering remains preserved for grouped records
- metadata filters such as `event_type`, date, subject, and authorization are applied before synthesis
- lexical, dense, and explicit filename matches are treated as standard retrieval features, not fallback branches

This is where the largest architectural shift should occur.

## 6. Introduce record-aware RAG as the main multi-document strategy

The code already has a useful precedent in `_group_sources_into_records()` and `_document_balanced_hits()` in `src/archive_manager/retrieval/query.py:242-265` and `src/archive_manager/retrieval/query.py:323-343`.

Promote that idea:

- retrieve by event or record when manifests exist
- preserve all pages for a manifest-backed event when answering record-level questions
- use chunk-level retrieval for legacy or ungrouped material
- let the answer layer reason over record-scoped evidence first, rather than asking many bespoke handlers to reconstruct records after retrieval

This preserves one of the strongest current design choices: event membership is determined structurally, not by the model.

## 7. Keep authorization and audit paths deterministic

Do not move these responsibilities into the model layer.

Preserve:

- visibility rules in `src/archive_manager/security/access_policy.py:7-32`
- authorized hit filtering in `src/archive_manager/retrieval/query.py:1370-1390`
- audit logging in `src/archive_manager/lifecycle/audit.py:14-32`
- trace logging in `src/archive_manager/lifecycle/trace_log.py:22-41`

RAG-first should change how evidence is selected and summarized, not who is allowed to see it.

## 8. Simplify the cache strategy around durable retrieval state

Today the system depends on:

- ingest cache: `src/archive_manager/ingestion/ingest.py:94-121`
- searchable sidecars and derived PDFs: `src/archive_manager/ingestion/ingest.py:592-634`
- EventFacts cache: `src/archive_manager/core/event_facts.py:67-84`

Migration goal:

- keep ingest cache only for idempotent ingestion and source lookup
- keep OCR and searchable files as durable derived artifacts
- reduce dependence on EventFacts as a required query-time cache
- treat vector payloads and source artifacts as the main retrieval substrate

That avoids ongoing cache-structure churn every time a new document family introduces new fields.

## 9. Redesign evaluation around retrieval quality and answer grounding, not intent count

Current evaluation is built around expected planner intents and answer substrings in:

- `src/archive_manager/evaluation/evaluation.py:12-51`
- `src/archive_manager/evaluation/evaluate_queries.py:11-21`

For a RAG-first system, evaluation should instead emphasize:

- retrieval relevance
- grounded answer quality
- correct provenance retention
- authorization correctness
- acceptable latency
- stability across multiple document domains

The current fixture style can remain, but intent matching should stop being the primary success criterion.

## 10. Roll out in phases, not as a single rewrite

Recommended sequence:

### Phase A: routing reduction

- reduce planner complexity
- keep existing deterministic handlers working
- default more questions to RAG

### Phase B: retrieval-first generalization

- strengthen grouped and event-aware retrieval
- enrich Qdrant payload metadata
- make retrieval the default for most content questions

### Phase C: parser demotion

- convert automotive and domain parsers into optional enrichment and validation
- stop adding new deterministic handlers for every new record type

### Phase D: cache simplification

- trim query-time dependency on EventFacts
- standardize around vector payload and OCR sidecar provenance

### Phase E: multi-domain expansion

- add new document families without first creating planner rules or full parsers
- only add structured extractors where they provide clear value

## 11. Preserve a small deterministic core long-term

After migration, the parts most worth keeping deterministic are:

- `access_policy.py`
- manifest lookup and grouping
- source inventory and scope narrowing
- deletion, reset, and admin lifecycle paths
- a few exact, business-critical reporting queries if needed

Everything else should default toward retrieval and grounded synthesis.

## 12. Definition of success

The migration is successful when:

- adding a new document type no longer requires adding planner intents first
- most user questions can be answered through retrieval without bespoke parser logic
- deterministic code is focused on governance and exact utilities, not domain expansion
- cached structured facts are optional accelerators, not mandatory dependencies
- grouped event retrieval remains reliable for manifest-backed records
- evaluation reflects grounding quality and cross-domain behavior rather than parser coverage

## Net recommendation

Do not replace the current architecture wholesale. Re-center it.

Keep the existing ingestion backbone, authorization model, manifests, Qdrant storage, and audit and trace systems. Migrate the query layer so that RAG is the default path, and treat parsers plus EventFacts as optional enrichment and validation layers rather than the primary answering mechanism.

That gives you lower maintenance as document types expand, without sacrificing the strongest control points already present in this repository.
