# Paperboy - TODO & Progress Tracker

This document tracks todo items, progress, blockers, and open questions.

---

## Current Tasks

| Status | Task | Priority | Notes |
|--------|------|----------|-------|
| [x] | Typesense search integration | High | Full-text search for papers |

**Status legend**: `[ ]` pending, `[~]` in progress, `[x]` completed, `[!]` blocked

---

## Typesense Integration Plan

### Overview

Add full-text search using [Typesense](https://typesense.org/) - a fast, typo-tolerant search engine. Users will be able to search papers by title, authors, abstract, and categories directly from the web interface.

### Architecture

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   Web UI        │────▶│   FastAPI       │────▶│   Typesense     │
│  (Search form)  │     │  /search API    │     │   (Docker)      │
└─────────────────┘     └─────────────────┘     └─────────────────┘
                               │
                               ▼
                        ┌─────────────────┐
                        │   SQLite DB     │
                        │ (paper content) │
                        └─────────────────┘
```

### Implementation Steps

#### Phase 1: Infrastructure Setup
- [x] 1.1 Add `typesense` Python client to requirements.txt
- [x] 1.2 Create docker-compose.yml with Typesense service
- [x] 1.3 Add Typesense config options to config.py

#### Phase 2: Indexing
- [x] 2.1 Create `index/sync_typesense.py` script
  - Define schema: paper_id, title, authors, abstract, categories, year, doi
  - Batch index papers from SQLite to Typesense
  - Support incremental updates
- [ ] 2.2 Test indexing with local database (requires Typesense running)

#### Phase 3: Search API
- [x] 3.1 Add search client to retriever.py or create search.py module
- [x] 3.2 Create `GET /search` endpoint in main.py
  - Query parameters: q (query), category, year, format, page, per_page
  - Return: hits with highlights, facets, pagination
- [x] 3.3 Create `GET /search/stats` for index statistics

#### Phase 4: Web Interface
- [x] 4.1 Update root HTML with search form
  - Search input with instant results
  - Category/year facet filters
  - Result cards with title, authors, abstract snippet
  - Click to download paper
- [x] 4.2 Add keyboard shortcuts (/ to focus search)

#### Phase 5: Documentation & Deployment
- [x] 5.1 Update README.md with Typesense setup instructions
- [x] 5.2 Update TODO.md with implementation progress
- [x] 5.3 Update START_HERE.md with search API docs

### Schema Design

```json
{
  "name": "papers",
  "fields": [
    {"name": "paper_id", "type": "string", "facet": false},
    {"name": "title", "type": "string", "facet": false},
    {"name": "authors", "type": "string", "facet": false},
    {"name": "abstract", "type": "string", "facet": false},
    {"name": "categories", "type": "string[]", "facet": true},
    {"name": "primary_category", "type": "string", "facet": true},
    {"name": "year", "type": "int32", "facet": true},
    {"name": "doi", "type": "string", "facet": false, "optional": true},
    {"name": "journal_ref", "type": "string", "facet": false, "optional": true},
    {"name": "file_type", "type": "string", "facet": true}
  ],
  "default_sorting_field": "year"
}
```

### Configuration Options

```env
# Typesense configuration
TYPESENSE_HOST=localhost
TYPESENSE_PORT=8108
TYPESENSE_PROTOCOL=http
TYPESENSE_API_KEY=your-api-key
TYPESENSE_ENABLED=true
```

### API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/search` | GET | Full-text search with filters |
| `/search/suggest` | GET | Autocomplete suggestions (optional) |

**Search parameters:**
- `q` - Search query (required)
- `category` - Filter by category
- `year_min`, `year_max` - Filter by year range
- `format` - Filter by file type (pdf/source)
- `page` - Page number (default: 1)
- `per_page` - Results per page (default: 20, max: 100)

**Response:**
```json
{
  "query": "dark matter cosmology",
  "found": 1234,
  "page": 1,
  "per_page": 20,
  "hits": [
    {
      "paper_id": "2103.06497",
      "title": "Dark Matter in the Universe",
      "authors": "A. Einstein, N. Bohr",
      "abstract": "We present a comprehensive study...",
      "categories": ["astro-ph.CO", "hep-ph"],
      "year": 2021,
      "file_type": "pdf",
      "highlights": {
        "title": "<mark>Dark Matter</mark> in the Universe",
        "abstract": "...comprehensive study of <mark>dark matter</mark>..."
      }
    }
  ],
  "facets": {
    "categories": [{"value": "astro-ph.CO", "count": 500}, ...],
    "year": [{"value": 2024, "count": 100}, ...]
  }
}
```

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

### 2026-01-14

**Typesense search integration**
- Added Typesense full-text search for papers
- Created `source/paperboy/search.py` - Typesense client with search, faceting, highlights
- Created `index/sync_typesense.py` - Script to sync SQLite to Typesense
- Added `/search` endpoint with query, category, year, format filters
- Added `/search/stats` endpoint for index statistics
- Updated web UI with search interface (tabbed Search/Download)
- Added keyboard shortcut (/) to focus search
- Updated docker-compose.yml with Typesense service
- Added Typesense configuration to config.py

**Kaggle metadata import**
- Created `index/import_kaggle_metadata.py` to import all metadata fields
- Imported 1.1M paper metadata from Kaggle dataset
- Added columns: categories, title, authors, abstract, doi, journal_ref, comments, submitter, report_no, versions
- 86.4% of papers now have full metadata
- 184 categories now available (34 legacy + 176 modern)

**Random paper API enhancements**
- Added `local_only` parameter to `/paper/random` endpoint
- Updated category filtering to search both paper_id and categories column

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
| 2026-01-14 | Import Kaggle metadata | 1.1M papers with full metadata |
| 2026-01-14 | Add local_only to random | Select from entire DB or local only |
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

- Vector/semantic search using embeddings
- Paper recommendations based on reading history
- Citation graph visualization
- BibTeX export endpoint

---

## Notes

- The database now has 1.27M+ papers indexed
- 1.1M papers have full metadata (title, authors, abstract, categories)
- Database is git-ignored; must be recreated or copied when setting up
- Index script uses MD5 hashing for deduplication
