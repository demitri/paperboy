# Paperboy

A Python microservice for efficiently delivering individual academic papers from arXiv's bulk tar archives using indexed database lookups.

## Overview

arXiv distributes papers in massive bulk tar archive files (multiple GB each). This service solves the problem of accessing individual papers without extracting entire archives by using SQLite indexing to enable instant retrieval through direct byte-level reads.

## Features

- **Fast Individual Paper Retrieval** - Extract papers in milliseconds without decompressing full archives
- **SQLite-Based Indexing** - O(1) lookup performance for paper locations
- **Multiple Format Support** - Retrieve PDFs or gzipped LaTeX source files
- **Web Interface** - User-friendly HTML search and download interface
- **REST API** - Programmatic access via clean API endpoints
- **Comprehensive Error Handling** - Helpful error messages with suggestions
- **Docker Support** - Containerized deployment ready for production
- **Efficient Storage** - No need to maintain extracted copies of archives

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
   - Returns paper to user

### Technology Stack

- **FastAPI** - Modern async web framework
- **Uvicorn** - ASGI server
- **SQLite3** - Embedded database for indexing
- **Pydantic** - Configuration and data validation
- **Python 3.9+** - Core runtime

## Installation

### Prerequisites

- Python 3.9 or higher
- arXiv bulk tar files organized by year
- Sufficient disk space for SQLite index (approximately 137 MB)

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
```

5. Build the index (one-time operation):
```bash
python index_arxiv_bulk_files.py --tar-dir /path/to/tar/files --db-path /path/to/arxiv_index.db
```

## Usage

### Starting the Service

```bash
uvicorn paperboy.main:app --host 0.0.0.0 --port 8000
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
```

Example:
```bash
curl http://localhost:8000/paper/2103.06497
```

#### Download Paper (Form Submission)
```bash
POST /download
Content-Type: application/x-www-form-urlencoded

paper_id=2103.06497
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

| Variable | Description | Required |
|----------|-------------|----------|
| `INDEX_DB_PATH` | Path to SQLite index database | Yes |
| `TAR_DIR_PATH` | Path to directory containing tar archives | Yes |

## Supported Paper ID Formats

- **Old format**: `hep-lat9107001`, `astro-ph9205002` (subject-class/YYMMNNN)
- **New format**: `1234.5678`, `2103.06497` (YYYY.NNNN)

## Project Structure

```
paperboy/
├── paperboy/
│   ├── __init__.py
│   ├── main.py           # FastAPI application
│   ├── config.py         # Configuration management
│   └── retriever.py      # Core paper extraction logic
├── index_arxiv_bulk_files.py  # Index builder script
├── extract_paper.py      # Standalone extraction utility
├── requirements.txt      # Python dependencies
├── Dockerfile           # Container configuration
├── .env.example         # Example environment configuration
└── README.md           # This file
```

## Database Schema

### `paper_index` Table
- `paper_id` - arXiv paper identifier (indexed)
- `archive_file` - Relative path to tar archive
- `offset` - Byte offset within tar file
- `size` - File size in bytes
- `file_type` - Paper format (pdf/gzip)
- `year` - Publication year (indexed)
- `timestamp` - Indexing timestamp

### `bulk_files` Table
- `file_path` - Archive file path
- `file_hash` - MD5 hash for deduplication
- `indexed_at` - Indexing timestamp

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
1. Queries SQLite database for paper location
2. Opens the tar file in binary read mode
3. Seeks to the exact byte offset
4. Reads the specified number of bytes
5. Returns the paper data directly to the client

This approach avoids decompressing entire archives, making retrieval nearly instantaneous.

## Error Handling

The service provides detailed error messages for common issues:
- **Empty Database** - Index hasn't been built yet
- **Paper Not Found** - ID doesn't exist (with similar ID suggestions)
- **Missing Archive Files** - Tar file has been moved or deleted
- **Permission Issues** - Insufficient file access permissions
- **Database Errors** - SQLite connection or query problems

All errors are presented with user-friendly HTML pages in the web interface.

## Performance

- **Lookup Time**: O(1) database query (milliseconds)
- **Extraction Time**: Direct byte read (milliseconds)
- **Index Size**: ~137 MB for complete arXiv archive
- **No Extraction Required**: Papers retrieved directly from tar files

## Development

### Running Tests

```bash
# Test indexing a single file
python index_arxiv_bulk_files.py --tar-dir /path/to/tar/files --db-path test.db

# Test extraction
python extract_paper.py --paper-id 2103.06497 --db-path test.db --verbose
```

### Local Development

```bash
uvicorn paperboy.main:app --reload --port 8000
```

## Deployment Considerations

- Mount tar archive directory as read-only volume
- Ensure SQLite database is on fast storage (SSD recommended)
- Configure appropriate file handle limits for production
- Consider using a reverse proxy (nginx) for production deployments
- Monitor disk I/O for performance optimization

## License

[Add your license information here]

## Contributing

[Add contribution guidelines here]

## Support

For issues and questions, please [add contact information or issue tracker link].
