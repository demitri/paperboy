# Paperboy

A Python microservice for efficiently delivering individual academic papers from arXiv's bulk tar archives using indexed database lookups.

## Overview

arXiv distributes papers in massive bulk tar archive files (multiple GB each). This service solves the problem of accessing individual papers without extracting entire archives by using SQLite indexing to enable instant retrieval through direct byte-level reads.

## Features

- **Fast Individual Paper Retrieval** - Extract papers in milliseconds without decompressing full archives
- **Full-Text Search** - Search by title, authors, abstract via Typesense
- **SQLite-Based Indexing** - O(1) lookup performance for paper locations
- **Multiple Format Support** - Retrieve PDFs or gzipped LaTeX source files
- **Web Interface** - Search interface with faceted filtering and instant results
- **REST API** - Programmatic access via clean API endpoints
- **Upstream Fallback** - Chain multiple Paperboy instances for distributed archives
- **arXiv Direct Fallback** - Fetch from arxiv.org when local/upstream unavailable
- **LRU Disk Cache** - Cache retrieved papers for offline access
- **Random Paper API** - Get random papers with format/category filtering
- **Metadata Headers** - Rich metadata in response headers for programmatic access
- **Docker Support** - Containerized deployment ready for production

## Architecture

### Data Flow

1. **Indexing Phase** (one-time setup):
   - Scan tar files organized by year (1991-present)
   - Record each paper's byte offset and size
   - Build SQLite index with hash-based deduplication

2. **Retrieval Phase** (runtime):
   - User requests paper via web UI or API
   - FastAPI queries SQLite database for paper location
   - Service reads specific byte range directly from tar file
   - Returns paper to user with metadata headers

### Retrieval Order

When a paper is requested, sources are tried in this order:
1. **Cache** - Local disk cache (if configured)
2. **Local tar files** - Direct read from indexed archives
3. **Upstream server** - Another Paperboy instance (if configured)
4. **arXiv.org** - Direct fetch from arxiv.org (if enabled)

### Technology Stack

- **FastAPI** - Modern async web framework
- **Uvicorn** - ASGI server
- **SQLite3** - Embedded database for indexing
- **Typesense** - Fast, typo-tolerant search engine
- **Pydantic** - Configuration and data validation
- **httpx** - HTTP client for upstream/arXiv requests
- **Python 3.9+** - Core runtime

## Installation

### Prerequisites

- Python 3.9 or higher
- arXiv bulk tar files organized by year
- Sufficient disk space for SQLite index (~150 MB for full archive)

### Setup

1. Clone the repository:
```bash
git clone <repository-url>
cd paperboy
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Create configuration file:
```bash
cp .env.example .env
```

4. Edit `.env` with your paths:
```env
INDEX_DB_PATH="/path/to/your/arxiv_index.db"
TAR_DIR_PATH="/path/to/your/arxiv/tar/files/"

# Optional: Upstream fallback server
UPSTREAM_SERVER_URL="http://upstream-paperboy:8000"
UPSTREAM_TIMEOUT=30.0
UPSTREAM_ENABLED=true

# Optional: arXiv direct fallback
ARXIV_FALLBACK_ENABLED=true
ARXIV_TIMEOUT=30.0

# Optional: Paper cache for offline access
CACHE_DIR_PATH="/path/to/cache"
CACHE_MAX_SIZE_GB=1.0

# Optional: Typesense search (requires running Typesense server)
TYPESENSE_HOST=localhost
TYPESENSE_PORT=8108
TYPESENSE_PROTOCOL=http
TYPESENSE_API_KEY=your-api-key
TYPESENSE_ENABLED=true
```

5. Build the index (one-time operation):
```bash
python index/index_arxiv_bulk_files.py /path/to/tar/files --db-path /path/to/arxiv_index.db
```

6. (Optional) Import metadata from Kaggle dataset:
```bash
# Download arxiv-metadata-oai-snapshot.json.zip from Kaggle first
python index/import_kaggle_metadata.py arxiv-metadata-oai-snapshot.json.zip /path/to/arxiv_index.db
```

## Usage

### Starting the Service

```bash
uvicorn source.paperboy.main:app --host 0.0.0.0 --port 8000
```

Or with Docker:
```bash
docker build -t paperboy .
docker run -p 8000:8000 \
  -v /path/to/arxiv_index.db:/data/arxiv_index.db \
  -v /path/to/tar/files:/data/tar_files \
  -e INDEX_DB_PATH=/data/arxiv_index.db \
  -e TAR_DIR_PATH=/data/tar_files \
  paperboy
```

### Web Interface

Navigate to `http://localhost:8000` in your browser to access the search interface.

### API Endpoints

#### Get Paper by ID
```bash
GET /paper/{paper_id}
GET /paper/{paper_id}?format=pdf      # PDF only
GET /paper/{paper_id}?format=source   # Source only (gzip/tar)
```

**Response Headers:**
- `X-Paper-ID` - Normalized paper ID
- `X-Paper-Format` - Format category (pdf, source)
- `X-Paper-File-Type` - Specific type (pdf, gzip, tar)
- `X-Paper-Year` - Publication year
- `X-Paper-Version` - Requested version (if specified)
- `X-Paper-Source` - Retrieval source (local, cache, upstream, arxiv_pdf)

Example:
```bash
curl http://localhost:8000/paper/2103.06497 -o paper.pdf
curl "http://localhost:8000/paper/2103.06497?format=source" -o paper.gz
```

#### Get Paper Metadata
```bash
GET /paper/{paper_id}/info
```

Returns JSON with paper metadata without downloading content:

#### Get IR Package (Intermediate Representation)
```bash
GET /paper/{paper_id}/ir                    # Default profile (text-only)
GET /paper/{paper_id}/ir?profile=text-only  # Text extraction optimized
GET /paper/{paper_id}/ir?profile=full       # Full LaTeXML output
```

Returns an IR package containing LaTeXML XML output and source files. IR packages are the canonical format for downstream processing (chunking, indexing, semantic extraction).

**Response:**
- Success (200): tar.gz archive with `Content-Type: application/gzip`
- Not Found (404): Paper not found
- Unprocessable (422): Paper is PDF-only or LaTeXML conversion failed

**IR Package Contents:**
- `manifest.json` - Package metadata (paper_id, profile, main_tex_file)
- `output.xml` - LaTeXML XML conversion of the LaTeX source
- `source/` - Original LaTeX source files

**Requirements:**
- Paper must have LaTeX source (PDF-only papers return 422)
- Requires `arxiv-src-ir` package installed
- LaTeXML must be available on the system

**Example:**
```bash
curl "http://localhost:8000/paper/2103.06497/ir" -o paper_ir.tar.gz
curl "http://localhost:8000/paper/2103.06497/ir?profile=full" -o paper_ir_full.tar.gz
```
```json
{
  "paper_id": "2103.06497",
  "file_type": "pdf",
  "format": "pdf",
  "size_bytes": 1234567,
  "year": 2021,
  "locally_available": true,
  "source": "local"
}
```

#### Get Random Paper
```bash
GET /paper/random                           # Random locally available paper
GET /paper/random?format=pdf                # Random PDF
GET /paper/random?category=astro-ph         # Random astrophysics paper
GET /paper/random?local_only=false          # Random from entire database
GET /paper/random?download=true             # Return paper content instead of metadata
```

#### List Available Categories
```bash
GET /paper/categories
```

Returns:
```json
{
  "legacy_categories": ["astro-ph", "hep-lat", ...],
  "modern_categories": ["astro-ph.GA", "cs.AI", ...],
  "all_categories": [...],
  "categories_column_exists": true
}
```

#### Search Papers (requires Typesense)
```bash
GET /search?q=dark+matter                    # Basic search
GET /search?q=neural+network&category=cs.AI  # With category filter
GET /search?q=cosmology&year_min=2020        # With year filter
GET /search?q=machine+learning&page=2        # Pagination
```

Returns:
```json
{
  "query": "dark matter",
  "found": 12345,
  "page": 1,
  "per_page": 20,
  "total_pages": 618,
  "hits": [
    {
      "paper_id": "2103.06497",
      "title": "Dark Matter Studies",
      "authors": "A. Einstein, N. Bohr",
      "abstract": "We present...",
      "categories": ["astro-ph.CO", "hep-ph"],
      "year": 2021,
      "highlights": {"title": "<mark>Dark Matter</mark> Studies"}
    }
  ],
  "facets": {
    "primary_category": [{"value": "astro-ph.CO", "count": 500}],
    "year": [{"value": 2024, "count": 100}]
  }
}
```

#### Health Check
```bash
GET /health
```

#### Debug Configuration
```bash
GET /debug/config
```

### Command-Line Extraction

Extract a single paper using the standalone utility:
```bash
python extract_paper.py --paper-id 2103.06497 --db-path /path/to/arxiv_index.db --output-dir ./output
```

## Configuration

Configuration is managed via environment variables or `.env` file:

| Variable | Description | Required | Default |
|----------|-------------|----------|---------|
| `INDEX_DB_PATH` | Path to SQLite index database | Yes | - |
| `TAR_DIR_PATH` | Path to directory containing tar archives | Yes | - |
| `UPSTREAM_SERVER_URL` | URL of upstream Paperboy server | No | None |
| `UPSTREAM_TIMEOUT` | Upstream request timeout (seconds) | No | 30.0 |
| `UPSTREAM_ENABLED` | Enable upstream fallback | No | true |
| `ARXIV_FALLBACK_ENABLED` | Enable direct arXiv.org fallback | No | true |
| `ARXIV_TIMEOUT` | arXiv request timeout (seconds) | No | 30.0 |
| `CACHE_DIR_PATH` | Directory for paper cache | No | None |
| `CACHE_MAX_SIZE_GB` | Maximum cache size in GB | No | 1.0 |
| `TYPESENSE_HOST` | Typesense server host | No | localhost |
| `TYPESENSE_PORT` | Typesense server port | No | 8108 |
| `TYPESENSE_PROTOCOL` | http or https | No | http |
| `TYPESENSE_API_KEY` | Typesense API key | No | None |
| `TYPESENSE_ENABLED` | Enable Typesense search | No | false |

## Supported Paper ID Formats

- **Modern format**: `1234.5678`, `2103.06497` (YYMM.NNNNN)
- **Old format**: `hep-lat9107001`, `astro-ph9205002` (category+YYMMNNN)
- **With slash**: `astro-ph/0412561`
- **With prefix**: `arXiv:2103.06497`
- **Full URLs**: `https://arxiv.org/abs/2103.06497`

**Note:** Version suffixes (e.g., `v2`, `v3`) are supported. If a specific version is not in the local database, the service will attempt to fetch it from arXiv.org directly (if arXiv fallback is enabled).

## Project Structure

```
paperboy/
├── AI notes/                 # AI agent documentation
│   ├── START_HERE.md         # Quick start for AI agents
│   └── TODO.md               # Task tracking
├── index/                    # Indexing components
│   ├── arXiv_manifest.sqlite3    # SQLite index (not committed)
│   ├── index_arxiv_bulk_files.py # Index builder script
│   ├── import_kaggle_metadata.py # Kaggle metadata importer
│   └── sync_typesense.py     # Typesense search indexer
├── source/paperboy/          # Main application
│   ├── main.py               # FastAPI endpoints
│   ├── retriever.py          # Paper retrieval logic
│   ├── ir.py                 # IR package generation
│   ├── search.py             # Typesense search client
│   ├── cache.py              # LRU disk cache
│   └── config.py             # Pydantic settings
├── docker/                   # Docker configuration
│   ├── docker-compose.yml    # Docker Compose with Typesense
│   └── Dockerfile            # Container configuration
├── extract_paper.py          # CLI extraction utility
├── requirements.txt          # Python dependencies
└── README.md                 # This file
```

## Database Schema

### `paper_index` Table

**Core fields (from indexing):**
- `paper_id` - arXiv paper identifier (PRIMARY KEY)
- `archive_file` - Relative path to tar archive
- `offset` - Byte offset within tar file
- `size` - File size in bytes
- `file_type` - Paper format (pdf/gzip/tar)
- `year` - Publication year (indexed)

**Metadata fields (from Kaggle import):**
- `categories` - Space-separated category list (indexed)
- `title` - Paper title
- `authors` - Author list
- `abstract` - Paper abstract
- `doi` - Digital Object Identifier (indexed)
- `journal_ref` - Journal reference
- `comments` - Author comments
- `submitter` - Who submitted the paper
- `report_no` - Report number
- `versions` - Available versions

### `bulk_files` Table
- `file_path` - Archive file path
- `file_hash` - MD5 hash for deduplication
- `indexed_at` - Indexing timestamp

## Importing Metadata from Kaggle

The [arXiv Dataset on Kaggle](https://www.kaggle.com/datasets/Cornell-University/arxiv) provides complete metadata for 3M+ papers as a ~1.5 GB JSONL file. The `import_kaggle_metadata.py` script imports all available fields:

**Fields imported:**
- `categories` - Paper categories (e.g., "astro-ph.GA hep-ph")
- `title` - Paper title
- `authors` - Author list
- `abstract` - Paper abstract
- `doi` - Digital Object Identifier
- `journal_ref` - Journal reference
- `comments` - Author comments (e.g., "37 pages, 15 figures")
- `submitter` - Who submitted the paper
- `report_no` - Report number
- `versions` - Available versions (e.g., "v1 v2 v3")

**Usage:**

```bash
# Download from Kaggle (requires Kaggle account)
# https://www.kaggle.com/datasets/Cornell-University/arxiv

# Import directly from zip file (no extraction needed)
python index/import_kaggle_metadata.py arxiv-metadata-oai-snapshot.json.zip /path/to/arxiv_index.db
```

**Alternative: arXiv OAI-PMH**

For incremental updates, arXiv supports [OAI-PMH](https://info.arxiv.org/help/oa/index.html) for bulk metadata harvesting:
- Base URL: `https://oaipmh.arxiv.org/oai`
- Supports category-based selective harvesting
- Updated daily with new submissions

## Setting Up Typesense Search

[Typesense](https://typesense.org/) provides fast, typo-tolerant full-text search. This is optional but enables searching papers by title, authors, and abstract.

### 1. Start Typesense

Using Docker Compose (recommended):
```bash
docker-compose -f docker/docker-compose.yml up -d typesense
```

Or standalone:
```bash
docker run -d --name typesense \
  -p 8108:8108 \
  -v typesense-data:/data \
  -e TYPESENSE_API_KEY=your-api-key \
  -e TYPESENSE_DATA_DIR=/data \
  typesense/typesense:27.1
```

### 2. Configure Paperboy

Add to your `.env`:
```env
TYPESENSE_HOST=localhost
TYPESENSE_PORT=8108
TYPESENSE_API_KEY=your-api-key
TYPESENSE_ENABLED=true
```

### 3. Index Papers

Sync papers from SQLite to Typesense:
```bash
# Index all papers with metadata
python index/sync_typesense.py --db-path /path/to/arxiv_index.db --api-key your-api-key

# Recreate index from scratch
python index/sync_typesense.py --db-path /path/to/arxiv_index.db --recreate

# Check index stats
python index/sync_typesense.py --db-path /path/to/arxiv_index.db --stats-only
```

**Note:** Only papers with metadata (title, authors, abstract) are indexed. Run `import_kaggle_metadata.py` first to populate metadata.

### 4. Search

Once indexed, search is available via:
- Web UI at `http://localhost:8000` (Search Papers tab)
- API at `GET /search?q=your+query`

## How It Works

### Indexing Process

The `index_arxiv_bulk_files.py` script:
1. Scans the tar directory structure organized by year
2. Opens each tar file and reads its table of contents
3. Extracts paper IDs from filenames
4. Records byte offsets and sizes without extracting files
5. Stores metadata in SQLite with indices for fast lookup
6. Uses MD5 hashing to skip already-processed archives

### Retrieval Process

The `PaperRetriever` class:
1. Checks cache for previously retrieved papers
2. Queries SQLite database for paper location
3. Opens the tar file in binary read mode
4. Seeks to the exact byte offset
5. Reads the specified number of bytes
6. Falls back to upstream or arXiv.org if not found locally
7. Returns the paper data with metadata headers

This approach avoids decompressing entire archives, making retrieval nearly instantaneous.

## Error Handling

The service provides detailed error messages including:
- **Paper Not Found** - ID doesn't exist (with similar ID suggestions)
- **Version Not Found** - Specific version unavailable
- **Format Unavailable** - Requested format not available
- **Missing Archive Files** - Tar file has been moved or deleted
- **Tar File Hints** - Suggests which tar file to download

Errors include a `tar_hint` field showing which archive file is needed:
```json
{
  "detail": {
    "error": "Paper not found",
    "tar_hint": {
      "year_dir": "2021",
      "pdf_pattern": "arXiv_pdf_2103_*.tar",
      "src_pattern": "arXiv_src_2103_*.tar"
    }
  }
}
```

## Performance

- **Lookup Time**: O(1) database query (milliseconds)
- **Extraction Time**: Direct byte read (milliseconds)
- **Index Size**: ~150 MB for complete arXiv archive
- **No Extraction Required**: Papers retrieved directly from tar files

## Development

### Running Tests

```bash
# Test indexing a single file
python index/index_arxiv_bulk_files.py /path/to/tar/files --db-path test.db

# Test extraction
python extract_paper.py --paper-id 2103.06497 --db-path test.db --verbose
```

### Local Development

```bash
uvicorn source.paperboy.main:app --reload --port 8000
```

## Deployment Considerations

- Mount tar archive directory as read-only volume
- Ensure SQLite database is on fast storage (SSD recommended)
- Configure cache directory for offline resilience
- Configure appropriate file handle limits for production
- Consider using a reverse proxy (nginx) for production deployments
- Monitor disk I/O for performance optimization

## External Resources

- [arXiv Bulk Data Access](https://info.arxiv.org/help/bulk_data.html) - Official bulk download documentation
- [arXiv OAI-PMH](https://info.arxiv.org/help/oa/index.html) - Metadata harvesting protocol
- [arXiv Dataset on Kaggle](https://www.kaggle.com/datasets/Cornell-University/arxiv) - Complete metadata (~1.7 GB)
- [arXiv API User Manual](https://info.arxiv.org/help/api/user-manual.html) - API documentation

## License

[Add your license information here]

## Contributing

[Add contribution guidelines here]

## Support

For issues and questions, please [add contact information or issue tracker link].
