# Plan: Add USPTO Patent Retrieval to Paperboy

> **Status: COMPLETED** (2026-02-13) — All items implemented and deployed. 14M+ patents indexed.

## Overview

Add a parallel retrieval path for USPTO patent documents alongside existing arXiv paper support. Patents are served as raw XML from bulk ZIP archives containing concatenated XML documents. The architecture keeps arXiv and USPTO code separate — no shared base classes or over-abstraction — just a new retriever, new endpoints, and a new indexer.

## Architecture

```
Existing arXiv path (unchanged):
  /paper/{paper_id}  →  PaperRetriever  →  paper_index table  →  .tar files

New USPTO path:
  /patent/{patent_id}  →  PatentRetriever  →  patent_index table  →  .zip files
```

Each uses a **separate SQLite database file** (arXiv: `arXiv_manifest.sqlite3`, USPTO: `uspto_manifest.sqlite3`). Both share the same upstream fallback pattern and same config object.

---

## Files to Create

### 1. `index/index_uspto_bulk_files.py` — Patent Indexer

**Pattern**: Follow `index/index_arxiv_bulk_files.py` closely.

**Key differences from arXiv indexer**:
- Scans ZIP files (not tar files) in `PTGRXML/` and `APPXML/` subdirectories
- Each ZIP contains a single large XML file with thousands of concatenated `<us-patent-grant>` or `<us-patent-application>` documents separated by `<?xml ...?>` declarations
- Must split on `<?xml` boundaries, then parse just the `<publication-reference>/<document-id>/<doc-number>` and `<kind>` from each patent (minimal parsing — no lxml needed, can use regex or simple XML)
- Records `(patent_id, archive_file, offset, size, doc_type, kind_code, year)` in `patent_index` table
- **Offset/size**: byte offset and length of each patent's XML block within the **uncompressed** XML content inside the ZIP. At retrieval time, the ZIP will be opened, the inner XML file read, and the offset/size used to slice out the specific patent.

**Schema**:
```sql
CREATE TABLE IF NOT EXISTS patent_index (
    patent_id TEXT PRIMARY KEY,
    archive_file TEXT NOT NULL,
    offset INTEGER NOT NULL,
    size INTEGER NOT NULL,
    doc_type TEXT NOT NULL DEFAULT 'grant',
    kind_code TEXT,
    year INTEGER,
    record_created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_patent_year ON patent_index(year);
CREATE INDEX IF NOT EXISTS idx_patent_archive ON patent_index(archive_file);
CREATE INDEX IF NOT EXISTS idx_patent_doc_type ON patent_index(doc_type);
```

**Also adds to `bulk_files` table** (same table as arXiv, file_path will naturally be different since it's under PTGRXML/ or APPXML/).

**CLI**:
```bash
python index/index_uspto_bulk_files.py /data/uspto --db-path $INDEX_DB_PATH
python index/index_uspto_bulk_files.py /data/uspto --db-path $INDEX_DB_PATH --single-file ipg230103.zip
```

**ProcessPoolExecutor**: Same parallel pattern — each worker opens one ZIP, splits the XML, extracts patent IDs and byte offsets, returns a result dataclass. Main thread does all DB writes.

### 2. `source/paperboy/patent_retriever.py` — Patent Retrieval Logic

**Structure**: Standalone module, similar to `retriever.py` but simpler (no arXiv fallback, no version handling, no cache initially).

**Functions**:

```python
def normalize_patent_id(patent_id: str) -> str:
    """
    Normalize patent ID to bare doc number.
    US11123456B2 → 11123456
    US20200123456A1 → 20200123456
    11123456 → 11123456
    Strip 'US' prefix, strip kind code suffix ([A-Z]\d?$).
    """

def parse_patent_id(patent_id: str) -> tuple[str, str | None]:
    """
    Parse patent ID into (bare_number, kind_code).
    US11123456B2 → ("11123456", "B2")
    11123456 → ("11123456", None)
    """
```

**Class**: `PatentRetriever`
```python
class PatentRetriever:
    def __init__(self, settings: Settings):
        # Uses settings.INDEX_DB_PATH (same DB as arXiv)
        # Uses settings.PATENT_BULK_DIR_PATH for ZIP file location
        # Uses settings.UPSTREAM_SERVER_URL for fallback

    def _lookup_patent_metadata(self, patent_id: str) -> dict | None:
        """Query patent_index table."""

    def _get_from_local(self, patent_id: str) -> bytes | None:
        """
        Open ZIP, read inner XML file, seek to offset, read size bytes.
        Returns raw XML bytes for a single patent.
        """

    def _get_from_upstream(self, patent_id: str) -> bytes | None:
        """GET {upstream}/patent/{patent_id}"""

    def get_patent_by_id(self, patent_id: str) -> dict:
        """
        Primary retrieval method.
        Returns dict with: content, content_type, error, patent_id, kind_code, doc_type, source
        Fallback chain: local ZIP → upstream server
        """

    def get_patent_info(self, patent_id: str) -> dict | None:
        """Metadata-only lookup for /patent/{id}/info endpoint."""
```

### 3. Modifications to existing files

#### `source/paperboy/config.py` — Add one setting:

```python
# USPTO patent bulk data
PATENT_BULK_DIR_PATH: Optional[str] = None
```

#### `source/paperboy/main.py` — Add patent endpoints:

Add at module level (alongside PaperRetriever init):
```python
from .patent_retriever import PatentRetriever

# Initialize patent retriever (optional — only if configured)
patent_retriever: Optional[PatentRetriever] = None
if settings.PATENT_BULK_DIR_PATH:
    try:
        patent_retriever = PatentRetriever(settings)
    except Exception as e:
        logger.warning(f"Patent retriever not available: {e}")
```

**New endpoints**:

```
GET /patent/{patent_id:path}/info   → JSON metadata
GET /patent/{patent_id:path}        → Raw XML content
```

Response headers: `X-Patent-ID`, `X-Patent-Kind-Code`, `X-Patent-Doc-Type`, `X-Patent-Source`
Content-Type: `application/xml`

If `PATENT_BULK_DIR_PATH` not configured, return 404 with message: `"USPTO patent retrieval is not configured. Set PATENT_BULK_DIR_PATH."`

**Update `/health`**: Add `patent_configured: bool` field.
**Update `/debug/config`**: Add `PATENT_BULK_DIR_PATH` to output.

#### `docker/docker-compose.yml` — Add USPTO volume mount:

```yaml
environment:
  - PATENT_BULK_DIR_PATH=/data/uspto
volumes:
  - /data/uspto:/data/uspto:ro
```

---

## Patent ID Normalization (shared function)

Used by both indexer and retriever. Lives in `patent_retriever.py` and imported by the indexer.

```
Input              → bare_number   kind_code
US11123456B2       → 11123456      B2
US20200123456A1    → 20200123456   A1
11123456           → 11123456      None
D0987654S          → D0987654      S
RE12345E           → RE12345       E
PP12345P3          → PP12345       P3
```

Logic:
1. Strip leading "US" (case-insensitive)
2. Match trailing kind code: one uppercase letter optionally followed by one digit (`[A-Z]\d?$`)
3. Return (everything before kind code, kind code)

---

## Retrieval Flow Detail

1. Client requests `GET /patent/US11123456B2`
2. `parse_patent_id("US11123456B2")` → `("11123456", "B2")`
3. Query: `SELECT archive_file, offset, size, kind_code FROM patent_index WHERE patent_id = '11123456'`
4. If kind code was specified and doesn't match DB kind_code, could optionally warn (but still return — the index stores the latest)
5. Open ZIP: `zipfile.ZipFile(archive_path)`, get the single inner XML filename, `zf.open(inner_name)`, `f.seek(offset)`, `f.read(size)`
6. Return XML bytes with `Content-Type: application/xml`

---

## What Does NOT Change

- `retriever.py` — untouched, arXiv logic stays as-is
- `ir.py`, `ir_cache.py`, `cache.py`, `search.py` — untouched
- `paper_index` table — untouched
- All existing `/paper/*` endpoints — untouched
- All existing tests — untouched

---

## Implementation Order

1. **`source/paperboy/patent_retriever.py`** — ID normalization + PatentRetriever class
2. **`source/paperboy/config.py`** — Add `PATENT_BULK_DIR_PATH`
3. **`source/paperboy/main.py`** — Add `/patent/` endpoints + init patent_retriever
4. **`index/index_uspto_bulk_files.py`** — Indexer script
5. **`docker/docker-compose.yml`** — Add volume mount
6. **Test** — Index a few ZIPs, hit the endpoints

---

## Testing Plan

```bash
# 1. Index a small batch
python index/index_uspto_bulk_files.py /data/uspto --db-path index/arXiv_manifest.sqlite3

# 2. Verify index
sqlite3 index/arXiv_manifest.sqlite3 "SELECT COUNT(*) FROM patent_index;"
sqlite3 index/arXiv_manifest.sqlite3 "SELECT * FROM patent_index LIMIT 5;"

# 3. Start server
PATENT_BULK_DIR_PATH=/data/uspto uvicorn source.paperboy.main:app --reload

# 4. Test endpoints
curl http://localhost:8000/patent/11123456/info
curl http://localhost:8000/patent/US11123456B2 -o patent.xml
curl http://localhost:8000/health  # should show patent_configured: true
```
