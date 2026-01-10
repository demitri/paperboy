# Paperboy - AI Agent Quick Start Guide

## What is Paperboy?

Paperboy is a Python microservice that delivers individual academic papers from arXiv's bulk tar archives using SQLite indexing. The key innovation is **instant retrieval** of papers without decompressing entire multi-gigabyte archive files.

**Core problem solved**: arXiv distributes papers in massive bulk tar files (multiple GB each). Paperboy uses byte-level direct reads from tar files, enabled by a pre-built SQLite index.

## Key Features for AI Agents

### 1. Metadata in Response Headers
Every paper retrieval includes metadata headers:
```
X-Paper-ID: 2103.06497
X-Paper-Format: source
X-Paper-File-Type: gzip
X-Paper-Year: 2021
X-Paper-Version: 2
X-Paper-Source: upstream
```

### 2. File Type Signaling
Papers are returned with correct `Content-Type`:
- `application/pdf` - PDF file
- `application/gzip` - Gzipped LaTeX source
- `application/x-tar` - Tar archive

Check `X-Paper-File-Type` header or call `/paper/{id}/info` before downloading.

### 3. Version Handling
**Important limitation:** arXiv bulk tar files only contain the latest version of each paper - version numbers are not preserved. Requesting a specific version (e.g., `v3`) will return 404 unless that version happens to be indexed.

```bash
GET /paper/2103.06497      # Returns latest available version
GET /paper/2103.06497v2    # Returns 404 (versions not in bulk archives)
```

For specific historical versions, use the arXiv API directly: `https://arxiv.org/abs/2103.06497v2`

### 4. Format Filtering
Request specific formats:
```bash
GET /paper/2103.06497?format=pdf      # PDF only, 404 if unavailable
GET /paper/2103.06497?format=source   # Source only (gzip/tar)
GET /paper/2103.06497?format=preferred # Whatever is available (default)
```

### 5. Metadata Endpoint
Get paper info without downloading:
```bash
GET /paper/2103.06497/info
```
Returns JSON with `paper_id`, `file_type`, `format`, `size_bytes`, `year`, `locally_available`, `source`.

---

## Project Structure

```
paperboy/
├── AI notes/               # AI agent documentation (you are here)
│   ├── START_HERE.md       # This file
│   └── TODO.md             # Task tracking, progress, blockers
│
├── index/                  # Indexing components
│   ├── arXiv_manifest.sqlite3    # SQLite index database (1.27M+ papers, not committed)
│   └── index_arxiv_bulk_files.py  # Script to build/update the index
│
├── source/paperboy/        # Main application code
│   ├── main.py             # FastAPI application (endpoints)
│   ├── retriever.py        # Paper retrieval logic
│   ├── cache.py            # LRU disk cache for offline access
│   └── config.py           # Pydantic settings configuration
│
├── extract_paper.py        # CLI tool to extract individual papers
├── Dockerfile              # Container deployment
└── pyproject.toml          # Python dependencies
```

## Important: Filepaths with Spaces

Filepaths in this project frequently contain spaces (e.g., `AI notes/`). Always use quotes when reading or referencing files in shell commands:

```bash
# Correct
cat "AI notes/START_HERE.md"

# Incorrect - will fail
cat AI notes/START_HERE.md
```

## How It Works

### Two-Phase Architecture

1. **Indexing Phase** (one-time setup via `index/index_arxiv_bulk_files.py`):
   - Scans tar archives and reads table of contents (no extraction)
   - Records byte offset and size of each paper in SQLite
   - Uses MD5 hashing for deduplication

2. **Retrieval Phase** (runtime via FastAPI):
   - O(1) SQLite lookup for paper location
   - Binary seek to exact byte offset in tar file
   - Direct read of paper bytes (milliseconds)

### Database Schema

**`paper_index`** table:
- `paper_id` (TEXT PRIMARY KEY) - arXiv identifier (e.g., "2103.06497")
- `archive_file` (TEXT) - Relative path to tar archive
- `offset` (INTEGER) - Byte offset within tar file
- `size` (INTEGER) - File size in bytes
- `file_type` (TEXT) - Format: pdf/gzip/tar/unknown
- `year` (INTEGER) - Publication year

### API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/paper/{paper_id}` | GET | **Primary endpoint** - Retrieve paper by ID |
| `/paper/{paper_id}/info` | GET | Get paper metadata without downloading |
| `/health` | GET | Health check for monitoring |
| `/debug/config` | GET | Debug configuration and cache stats |
| `/` | GET | HTML search form (for humans) |
| `/download` | POST | Form submission handler (for humans) |

### API Reference for AI Agents

Interactive API documentation is available at `/docs` (Swagger UI) or `/redoc` when the server is running.

#### GET /paper/{paper_id}

**This is the primary endpoint for programmatic access.**

Retrieves raw paper content (PDF or gzipped LaTeX source) by arXiv ID.

**Paper ID formats accepted:**
- `1501.00963` - Modern arXiv ID (YYMM.NNNNN)
- `arXiv:1501.00963v3` - With prefix and version (version is respected - returns 404 if not found)
- `astro-ph/0412561` - Old format with category and slash
- `astro-ph0412561` - Old format without slash
- `https://arxiv.org/abs/1501.00963` - Full arXiv URL

**Query parameters:**
- `format` - Filter by format:
  - `pdf` - Only return PDF, 404 if unavailable
  - `source` - Only return source (gzip/tar), 404 if unavailable
  - `preferred` - Return whatever is available (default)

**Version handling:**
- arXiv bulk archives only contain the latest version - version suffixes are not preserved
- Requesting a specific version (e.g., `1501.00963v2`) will return 404
- Omit the version suffix to get the latest available version
- For historical versions, use arXiv directly: `https://arxiv.org/pdf/1501.00963v2.pdf`

**Response:**
- Success (200): Raw binary content with correct Content-Type:
  - `application/pdf` for PDF files
  - `application/gzip` for gzip-compressed LaTeX source
  - `application/x-tar` for tar archives
- Not Found (404): Paper not found, version not found, or format unavailable

**Response headers (metadata):**
- `X-Paper-ID`: Normalized paper ID
- `X-Paper-Format`: Format category (pdf, source, unknown)
- `X-Paper-File-Type`: Specific file type (pdf, gzip, tar, unknown)
- `X-Paper-Year`: Publication year (if known)
- `X-Paper-Version`: Requested version (if specified)
- `X-Paper-Source`: Where paper was retrieved from (local, cache, upstream)

**Example requests:**
```bash
# Get paper (any format)
curl http://localhost:8000/paper/2103.06497 --output paper.pdf

# Request specific format
curl "http://localhost:8000/paper/2103.06497?format=pdf" --output paper.pdf
curl "http://localhost:8000/paper/2103.06497?format=source" --output paper.gz

# Request specific version
curl http://localhost:8000/paper/2103.06497v2 --output paper.pdf
```

#### GET /paper/{paper_id}/info

Get metadata about a paper without downloading its content. Use this to check availability and format before downloading.

Checks local database first, then upstream server if configured. This means you can get metadata for papers that are only available upstream.

**Response (JSON):**
```json
{
  "paper_id": "2103.06497",
  "requested_version": null,
  "file_type": "pdf",
  "format": "pdf",
  "size_bytes": 1234567,
  "year": 2021,
  "locally_available": true,
  "upstream_configured": true,
  "source": "local"
}
```

The `source` field indicates where the metadata came from: `"local"` or `"upstream"`.

**Example:**
```bash
curl http://localhost:8000/paper/2103.06497/info
```

#### GET /health

Returns service health status as JSON.

**Response fields:**
- `status`: "healthy" or "unhealthy"
- `startup_error`: Error message if service failed to start
- `upstream_configured`: Whether fallback server is configured
- `upstream_enabled`: Whether fallback is enabled
- `cache_configured`: Whether paper caching is enabled

#### GET /debug/config

Returns full configuration details and cache statistics as JSON.

## Configuration

Via `.env` file or environment variables:

**Required:**
- `INDEX_DB_PATH` - Path to SQLite index database
- `TAR_DIR_PATH` - Path to directory containing tar archives

**Optional - Upstream fallback:**
- `UPSTREAM_SERVER_URL` - URL of upstream Paperboy server for fallback
- `UPSTREAM_TIMEOUT` - Timeout in seconds (default: 30.0)
- `UPSTREAM_ENABLED` - Enable/disable upstream fallback (default: true)

**Optional - Caching:**
- `CACHE_DIR_PATH` - Directory for paper cache (enables offline access)
- `CACHE_MAX_SIZE_GB` - Maximum cache size in GB (default: 1.0)

The cache stores papers retrieved from upstream or local archives. When the cache is full, least recently used papers are evicted first (LRU policy).

## Key Files to Understand

1. **`source/paperboy/retriever.py`** - Core paper retrieval logic with detailed error handling
2. **`source/paperboy/main.py`** - FastAPI endpoints (see `/docs` for interactive API docs)
3. **`source/paperboy/cache.py`** - LRU disk cache for offline paper access
4. **`index/index_arxiv_bulk_files.py`** - Index building script

## Supported Paper ID Formats

**Modern format (post-2007):**
- `1234.5678` or `2103.06497`

**Old format (pre-2007):**
- `hep-lat9107001`, `astro-ph9205002`
- With slash: `astro-ph/9205002`

**With prefixes:**
- `arXiv:2103.06497`
- `arxiv:astro-ph/0412561`

**Full URLs:**
- `https://arxiv.org/abs/2103.06497`
- `https://arxiv.org/pdf/2103.06497.pdf`

**Note:** Version suffixes (e.g., `v2`, `v3`) are accepted but will return 404 - bulk archives only contain the latest version. Strip the version suffix to retrieve papers.

## Current Status

See [TODO.md](TODO.md) for current tasks, progress, blockers, and open questions.

## Running the Application

```bash
# Install dependencies
pip install -e .

# Run the server
uvicorn source.paperboy.main:app --reload
```

## Updating the Index with New Tar Files

The indexing script is **idempotent** - it uses MD5 hashing to track which files have been processed and automatically skips them. This makes updates safe and efficient.

### Prerequisites

Tar files must be organized by year:
```
/path/to/arxiv/
├── 2024/
│   ├── arXiv_pdf_2401_001.tar
│   ├── arXiv_pdf_2401_002.tar
│   └── ...
├── 2025/
│   ├── arXiv_pdf_2501_001.tar
│   └── ...
```

### Full Directory Scan (Recommended)

Run the indexer pointing to your tar directory. It will:
- Scan all year directories
- Skip already-processed files (via MD5 hash check)
- Index only new or modified files

```bash
# Uses paths from .env: INDEX_DB_PATH and TAR_DIR_PATH
python index/index_arxiv_bulk_files.py $TAR_DIR_PATH --db-path $INDEX_DB_PATH
```

Add `-v` for verbose output to see detailed progress.

### Single File Indexing

To index just one new tar file:

```bash
# By filename (looks in appropriate year directory based on filename)
python index/index_arxiv_bulk_files.py $TAR_DIR_PATH --db-path $INDEX_DB_PATH --single-file arXiv_pdf_2501_001.tar

# By absolute path
python index/index_arxiv_bulk_files.py $TAR_DIR_PATH --db-path $INDEX_DB_PATH --single-file /path/to/arxiv/2025/arXiv_pdf_2501_001.tar
```

### What the Indexer Does

1. Opens each tar file and reads the table of contents (no extraction)
2. For each paper entry, records:
   - `paper_id` - Extracted from filename
   - `offset` - Byte position in tar file
   - `size` - File size in bytes
   - `file_type` - pdf/gzip/tar/unknown
   - `year` - From directory or filename
3. Stores MD5 hash of tar file to detect changes
4. Prints summary statistics when complete

### Verifying the Update

After indexing, check the database:

```bash
sqlite3 $INDEX_DB_PATH "SELECT COUNT(*) FROM paper_index;"
sqlite3 $INDEX_DB_PATH "SELECT COUNT(*) FROM bulk_files;"
```
