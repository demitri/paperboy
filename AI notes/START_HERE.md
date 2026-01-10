# Paperboy - AI Agent Quick Start Guide

## What is Paperboy?

Paperboy is a Python microservice that delivers individual academic papers from arXiv's bulk tar archives using SQLite indexing. The key innovation is **instant retrieval** of papers without decompressing entire multi-gigabyte archive files.

**Core problem solved**: arXiv distributes papers in massive bulk tar files (multiple GB each). Paperboy uses byte-level direct reads from tar files, enabled by a pre-built SQLite index.

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
| `/` | GET | HTML search form |
| `/paper/{paper_id}` | GET | Retrieve paper by ID |
| `/download` | POST | Form submission handler |
| `/debug/config` | GET | Debug configuration |

## Configuration

Via `.env` file or environment variables:
- `INDEX_DB_PATH` - Path to SQLite index database
- `TAR_DIR_PATH` - Path to directory containing tar archives

## Key Files to Understand

1. **`source/paperboy/retriever.py`** - Core paper retrieval logic with detailed error handling
2. **`source/paperboy/main.py`** - FastAPI endpoints
3. **`index/index_arxiv_bulk_files.py`** - Index building script

## Supported Paper ID Formats

- Old format: `hep-lat9107001`, `astro-ph9205002`
- New format: `1234.5678`, `2103.06497`

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
