# Paperboy - TODO & Progress Tracker

This document tracks todo items, progress, blockers, and open questions.

---

## Current Tasks

| Status | Task | Priority | Notes |
|--------|------|----------|-------|
| - | (No active tasks) | - | - |

**Status legend**: `[ ]` pending, `[~]` in progress, `[x]` completed, `[!]` blocked

---

## Open Questions

*Questions that need answers before proceeding:*

- (None currently)

---

## Blockers

*Issues preventing progress:*

- (None currently)

---

## Progress History

### 2026-01-10

**API enhancements**
- Added `GET /paper/{paper_id}/info` metadata endpoint
- Added `?format=` query parameter (pdf, source, preferred) to filter by format
- Now returns correct `Content-Type` header (application/pdf, application/gzip, application/x-tar)
- Version numbers in paper IDs are now respected (e.g., `2103.06497v2` returns exact version or 404)
- Added metadata response headers to paper retrieval endpoint:
  - `X-Paper-ID`: Normalized paper ID
  - `X-Paper-Format`: Format category (pdf, source, unknown)
  - `X-Paper-File-Type`: Specific file type (pdf, gzip, tar, unknown)
  - `X-Paper-Year`: Publication year (if known)
  - `X-Paper-Version`: Requested version (if specified)
  - `X-Paper-Source`: Where paper was retrieved from (local, cache, upstream)
- `/info` endpoint now checks upstream when local lookup fails
- `get_source_by_id()` returns full metadata dict including year, version, file_type

**Cache functionality**
- Added LRU disk cache for offline paper retrieval (`source/paperboy/cache.py`)
- New config options: `CACHE_DIR_PATH`, `CACHE_MAX_SIZE_GB`
- Papers retrieved from upstream or local archives are cached automatically
- Least recently used papers evicted when cache is full

**Documentation**
- Updated START_HERE.md with full API reference for AI agents
- Added format parameter, version handling, and metadata endpoint docs
- Documented response headers with metadata
- Updated supported paper ID formats section

### 2026-01-08

**Project reorganization**
- Created `AI notes/` directory for AI agent documentation
- Created `index/` directory for indexing components
- Moved `arxiv_index.db` to `index/`
- Moved `index_arxiv_bulk_files.py` to `index/`
- Created this TODO tracker and START_HERE.md quick start guide

**Documentation update**
- Added "Updating the Index with New Tar Files" section to START_HERE.md
- Documents full directory scan and single-file indexing procedures
- Includes verification commands

---

## Completed Tasks

| Date | Task | Notes |
|------|------|-------|
| 2026-01-10 | Add metadata response headers | X-Paper-ID/Format/File-Type/Year/Version/Source headers |
| 2026-01-10 | /info checks upstream | Falls back to upstream when local lookup fails |
| 2026-01-10 | Rich metadata in retrieval | get_source_by_id() returns full metadata dict |
| 2026-01-10 | Add paper cache | LRU disk cache with configurable size limit |
| 2026-01-10 | Add format query parameter | Filter by pdf/source/preferred |
| 2026-01-10 | Add metadata endpoint | GET /paper/{id}/info returns JSON metadata |
| 2026-01-10 | Respect version numbers | Versioned IDs return exact version or 404 |
| 2026-01-10 | Return correct Content-Type | PDF, gzip, tar detected and returned |
| 2026-01-08 | Project reorganization | Created AI notes/, index/ directories; moved indexing files |
| 2026-01-08 | Document index update procedure | Added to START_HERE.md |

---

## Ideas / Future Improvements

*Captured ideas for potential future work:*

- (Add ideas here as they come up)

---

## Notes

- The `arxiv_index.db` is ~137 MB and indexes 884,524 papers
- Database is git-ignored; must be recreated or copied when setting up
- Index script uses MD5 hashing for deduplication
