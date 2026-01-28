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

### 6. Full-Text Search (Optional)
When Typesense is configured, search 1.1M+ papers by title, authors, abstract:
```bash
GET /search?q=dark+matter+cosmology
```
Returns hits with highlights, faceted filters (category, year, file type), and pagination.

### 7. IR Package Generation
Get preprocessed papers as IR (Intermediate Representation) packages for downstream processing:
```bash
GET /paper/{paper_id}/ir                    # Default profile (text-only)
GET /paper/{paper_id}/ir?profile=text-only  # Optimized for text extraction
GET /paper/{paper_id}/ir?profile=full       # Full LaTeXML output
```

IR packages contain:
- LaTeXML XML output (structured representation of LaTeX)
- Original source files
- Manifest with metadata

**Important:** Only works for papers with LaTeX source. PDF-only papers return 422.

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
│   ├── index_arxiv_bulk_files.py  # Script to build/update the index
│   ├── import_kaggle_metadata.py # Import metadata from Kaggle dataset
│   └── sync_typesense.py         # Sync SQLite to Typesense search engine
│
├── source/paperboy/        # Main application code
│   ├── main.py             # FastAPI application (endpoints)
│   ├── retriever.py        # Paper retrieval logic
│   ├── ir.py               # IR package generation (LaTeXML)
│   ├── search.py           # Typesense search client
│   ├── cache.py            # LRU disk cache for offline access
│   └── config.py           # Pydantic settings configuration
│
├── docker/                 # Docker configuration
│   ├── docker-compose.yml  # Docker Compose with Typesense
│   └── Dockerfile          # Container configuration
├── extract_paper.py        # CLI tool to extract individual papers
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

**`paper_index`** table (core fields from indexing):
- `paper_id` (TEXT PRIMARY KEY) - arXiv identifier (e.g., "2103.06497")
- `archive_file` (TEXT) - Relative path to tar archive
- `offset` (INTEGER) - Byte offset within tar file
- `size` (INTEGER) - File size in bytes
- `file_type` (TEXT) - Format: pdf/gzip/tar/unknown
- `year` (INTEGER) - Publication year

**Metadata fields (from Kaggle import):**
- `categories` (TEXT) - Space-separated categories (e.g., "astro-ph.GA hep-ph")
- `title` (TEXT) - Paper title
- `authors` (TEXT) - Author list
- `abstract` (TEXT) - Paper abstract
- `doi` (TEXT) - Digital Object Identifier
- `journal_ref` (TEXT) - Journal reference
- `comments` (TEXT) - Author comments
- `versions` (TEXT) - Available versions (e.g., "v1 v2 v3")

### API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/paper/{paper_id}` | GET | **Primary endpoint** - Retrieve paper by ID |
| `/paper/{paper_id}/info` | GET | Get paper metadata without downloading |
| `/paper/random` | GET | Get a random paper from local archives |
| `/paper/categories` | GET | List available categories (legacy and modern) |
| `/search` | GET | Full-text search with filters (requires Typesense) |
| `/paper/{paper_id}/ir` | GET | **IR endpoint** - Get preprocessed IR package |
| `/search/stats` | GET | Search index statistics |
| `/health` | GET | Health check for monitoring |
| `/debug/config` | GET | Debug configuration and cache stats |
| `/` | GET | HTML search/download interface (for humans) |
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

#### GET /paper/random

Get a random paper from locally available tar files. Useful for sampling, testing, or exploration.

**Query parameters:**
- `format`: `pdf` or `source` - filter by file type
- `category`: e.g., `astro-ph`, `hep-lat` - filter by category (old-format papers only)
- `download`: `true` to return paper content, `false` (default) for metadata only

**Response (metadata mode):**
```json
{
  "paper_id": "1501.02345",
  "archive_file": "2015/arXiv_src_1501_001.tar",
  "file_type": "gzip",
  "format": "source",
  "size_bytes": 123456,
  "year": 2015,
  "locally_available": true
}
```

**Examples:**
```bash
# Get random paper metadata
curl http://localhost:8000/paper/random

# Get random PDF metadata
curl "http://localhost:8000/paper/random?format=pdf"

# Download a random source file
curl "http://localhost:8000/paper/random?format=source&download=true" -o paper.gz
```

**Note:** Category filtering only works for old-format papers (pre-2007) where the category is embedded in the paper ID (e.g., `astro-ph0412561`). Modern papers (YYMM.NNNNN format) don't include category in the ID.

#### GET /paper/categories

List available paper categories extracted from old-format paper IDs.

**Response:**
```json
{
  "categories": ["astro-ph", "cond-mat", "hep-lat", "hep-ph", "hep-th", ...],
  "count": 34
}
```

#### GET /search

Full-text search across 1.1M+ papers with faceted filtering. **Requires Typesense to be running and configured.**

**Query parameters:**
- `q` (required) - Search query string. Supports field-specific searches:
  - `author:einstein` - search authors field
  - `title:dark matter` - search title field
  - `abstract:cosmology` - search abstract field
  - `category:hep-th` - search categories field
  - `author:einstein relativity` - combine field + general search
- `category` - Filter by category (e.g., "astro-ph", "cs.AI")
- `year_min` - Minimum publication year
- `year_max` - Maximum publication year
- `format` - Filter by file type: "pdf" or "source"
- `page` - Page number (default: 1)
- `per_page` - Results per page (default: 20, max: 100)

**Response (JSON):**
```json
{
  "query": "dark matter cosmology",
  "found": 1234,
  "page": 1,
  "per_page": 20,
  "total_pages": 62,
  "hits": [
    {
      "paper_id": "2103.06497",
      "title": "Dark Matter in the Universe",
      "authors": "A. Einstein, N. Bohr",
      "abstract": "We present a comprehensive study...",
      "categories": ["astro-ph.CO", "hep-ph"],
      "primary_category": "astro-ph.CO",
      "year": 2021,
      "file_type": "pdf",
      "doi": "10.1234/example",
      "highlights": {
        "title": "<mark>Dark Matter</mark> in the Universe",
        "abstract": "...comprehensive study of <mark>dark matter</mark>..."
      }
    }
  ],
  "facets": {
    "primary_category": [{"value": "astro-ph.CO", "count": 500}, ...],
    "year": [{"value": 2024, "count": 100}, ...],
    "file_type": [{"value": "pdf", "count": 800}, ...]
  },
  "search_time_ms": 12
}
```

**Error responses:**
- Returns `{"error": "Search is not available"}` if Typesense is not configured
- Returns `{"error": "Search index not found..."}` if collection doesn't exist

**Examples:**
```bash
# Basic search
curl "http://localhost:8000/search?q=neural+networks"

# Field-specific search
curl "http://localhost:8000/search?q=author:einstein"
curl "http://localhost:8000/search?q=title:dark+matter"

# Search with category filter
curl "http://localhost:8000/search?q=galaxy+formation&category=astro-ph"

# Search with year range
curl "http://localhost:8000/search?q=machine+learning&year_min=2020&year_max=2024"

# Paginated results
curl "http://localhost:8000/search?q=quantum&page=2&per_page=50"
```

#### GET /search/stats

Get search index statistics.

**Response (JSON):**
```json
{
  "available": true,
  "collection": "papers",
  "num_documents": 1100000,
  "fields": 10
}
```

If Typesense is not available:
```json
{
  "available": false,
  "error": "Not connected"
}
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

#### GET /paper/{paper_id}/ir

**Generate an IR (Intermediate Representation) package for a paper.**

This endpoint converts LaTeX source to a preprocessed format suitable for downstream processing (chunking, semantic extraction, indexing). The IR package contains LaTeXML XML output plus the original source files.

**Query parameters:**
- `profile` - IR generation profile:
  - `text-only` (default) - Optimized for text extraction, smaller output
  - `full` - Complete LaTeXML output with all structural information

**Response:**
- Success (200): tar.gz archive containing:
  - `manifest.json` - Package metadata (paper_id, profile, main_tex_file, created_at)
  - `output.xml` - LaTeXML XML conversion
  - `source/` - Original LaTeX source files (.tex, .bbl, .bib, .sty, etc.)
- Not Found (404): Paper not found in index
- Unprocessable (422): IR generation failed. Common causes:
  - Paper is PDF-only (no LaTeX source available)
  - LaTeXML conversion failed (malformed LaTeX)
  - No LaTeX files found in source archive

**Response headers:**
- `Content-Type: application/gzip`
- `Content-Disposition: attachment; filename="{paper_id}.ir.tar.gz"`

**Error response (422):**
```json
{
  "detail": {
    "error": "ir_generation_failed",
    "message": "Content is PDF, not LaTeX source",
    "paper_id": "2103.06497"
  }
}
```

**Examples:**
```bash
# Get IR package with default profile
curl "http://localhost:8000/paper/2103.06497/ir" -o paper.ir.tar.gz

# Get IR package with full profile
curl "http://localhost:8000/paper/2103.06497/ir?profile=full" -o paper_full.ir.tar.gz

# Check for errors
curl -f "http://localhost:8000/paper/2103.06497/ir" -o paper.ir.tar.gz || echo "IR generation failed"
```

**Dependencies:**
- `arxiv-src-ir` package must be installed (`pip install arxiv-src-ir` or from source)
- LaTeXML must be available on the system (see arxiv-src-ir installation docs)

**Performance:**
- IR generation takes 5-30 seconds depending on paper complexity
- The endpoint uses a 120-second timeout for LaTeXML processing
- Consider caching IR packages for frequently accessed papers

**Integration with tesseretica:**
```python
from tesseretica.ingestion.retrievers import ARXIVDocumentRetriever
from tesseretica.documents import ArxivIRDocument

with ARXIVDocumentRetriever() as retriever:
    ir_doc = retriever.fetchIRDocument("2103.06497", profile="text-only")

    # Access IR package contents
    print(ir_doc.primary_tex_content)  # Main .tex file
    print(ir_doc.xml_content)          # LaTeXML XML
    print(ir_doc.latex_files)          # All source files
    print(ir_doc.structure_hints)      # Section hierarchy for chunking
```

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

**Optional - Typesense Search:**
- `TYPESENSE_ENABLED` - Enable search functionality (default: false)
- `TYPESENSE_HOST` - Typesense server host (default: localhost)
- `TYPESENSE_PORT` - Typesense server port (default: 8108)
- `TYPESENSE_PROTOCOL` - http or https (default: http)
- `TYPESENSE_API_KEY` - API key for Typesense authentication
- `TYPESENSE_COLLECTION` - Collection name (default: papers)

To enable search:
1. Start Typesense: `docker-compose -f docker/docker-compose.yml up -d typesense`
2. Set `TYPESENSE_ENABLED=true` and `TYPESENSE_API_KEY=your-key`
3. Sync the database: `python index/sync_typesense.py --db-path $INDEX_DB_PATH --api-key your-key`

**Optional - IR Package Generation:**

The `/paper/{paper_id}/ir` endpoint requires additional setup:

1. Install the `arxiv-src-ir` package:
   ```bash
   # From PyPI (when available)
   pip install arxiv-src-ir

   # Or from source
   cd /path/to/arxiv-src-ir/python
   pip install -e .
   ```

2. Install LaTeXML on the system:
   ```bash
   # macOS (Homebrew)
   brew install latexml

   # Ubuntu/Debian
   apt-get install latexml

   # Or from source (for specific version)
   # See: https://dlmf.nist.gov/LaTeXML/get.html
   ```

3. **Set the `LATEXML_BIN` environment variable** (required):
   ```bash
   # Find where latexml is installed
   which latexml

   # Set the environment variable (add to .env file or shell profile)
   export LATEXML_BIN=/usr/bin/latexml
   ```

   Add to your `.env` file:
   ```env
   LATEXML_BIN=/usr/bin/latexml
   ```

4. Verify LaTeXML is working:
   ```bash
   $LATEXML_BIN --VERSION
   ```

5. Test the IR endpoint:
   ```bash
   curl "http://localhost:8000/paper/2103.06497/ir" -o test.ir.tar.gz
   tar -tzf test.ir.tar.gz  # Should list: manifest.json, ir/latexml.xml, source/...
   ```

**Error messages:**
- If `arxiv-src-ir` is not installed: `"arxiv_src_ir package not installed"`
- If `LATEXML_BIN` is not set: `"LaTeXML not configured. Set LATEXML_BIN environment variable..."`
- If paper is PDF-only: `"Paper is not available as LaTeX source. IR packages require source, not PDF."`

## Key Files to Understand

1. **`source/paperboy/retriever.py`** - Core paper retrieval logic with detailed error handling
2. **`source/paperboy/main.py`** - FastAPI endpoints (see `/docs` for interactive API docs)
3. **`source/paperboy/ir.py`** - IR package generation (LaTeX extraction, main tex identification, arxiv-src-ir integration)
4. **`source/paperboy/search.py`** - Typesense search client with faceting and highlights
4. **`source/paperboy/cache.py`** - LRU disk cache for offline paper access
5. **`index/index_arxiv_bulk_files.py`** - Index building script
6. **`index/sync_typesense.py`** - Sync SQLite to Typesense for full-text search

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

### Local Development

```bash
# Install dependencies
pip install -e .

# Set environment variables (or use .env file)
export LATEXML_BIN=/usr/bin/latexml

# Run the server
uvicorn source.paperboy.main:app --reload
```

### Docker Deployment (Production)

Paperboy runs in Docker in production. The Docker image includes LaTeXML and arxiv-src-ir for IR package generation.

**Prerequisites:**
- Docker and Docker Compose installed
- `arxiv-src-ir` repository cloned at `/home/demitri/repositories/arxiv-src-ir`
  (or set `ARXIV_SRC_IR_PATH` to its location)

**Build and deploy:**

```bash
cd /home/demitri/repositories/paperboy

# Build and restart in one command
./docker/build.sh deploy

# Or build only (without restarting)
./docker/build.sh

# Then deploy manually
docker compose -f docker/docker-compose.yml up -d
```

**What the build script does:**
1. Copies `arxiv-src-ir/python` into the build context (Docker can't follow symlinks)
2. Builds the Docker image with LaTeXML and arxiv-src-ir installed
3. Cleans up the temporary copy
4. Optionally restarts the container (with `deploy` argument)

**Check status:**

```bash
# View running container
docker ps | grep paperboy

# View logs
docker compose -f docker/docker-compose.yml logs -f

# Health check
curl http://localhost:8000/health
```

**Environment variables** are loaded from `.env` file. Container-specific paths are overridden in `docker-compose.yml`.

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
