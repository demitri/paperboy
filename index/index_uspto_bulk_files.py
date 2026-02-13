#!/usr/bin/env python3
"""
Script to build SQLite index for USPTO patent bulk ZIP archives.

USPTO distributes patent data as ZIP files containing a single large XML file
with thousands of concatenated patent documents. Each document is separated
by <?xml ...?> declarations and is NOT valid XML as a whole.

This script scans the ZIP files, splits the inner XML on <?xml boundaries,
extracts the patent number and kind code from each document, and records
byte offsets in a SQLite patent_index table for efficient retrieval.
"""

import argparse
import hashlib
import logging
import multiprocessing
import os
import re
import sqlite3
import zipfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

logging.basicConfig(
    level=logging.WARNING,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Patent ID extraction (minimal parsing — regex, no lxml needed)
# ---------------------------------------------------------------------------

# Regex to extract doc-number from <publication-reference>
# Matches: <publication-reference>...<doc-number>XXXXX</doc-number>...</publication-reference>
_PUB_REF_RE = re.compile(
    rb'<publication-reference\b[^>]*>.*?'
    rb'<doc-number>\s*([A-Z]*\d+)\s*</doc-number>'
    rb'.*?</publication-reference>',
    re.DOTALL,
)

# Regex to extract kind code from <publication-reference>
_KIND_RE = re.compile(
    rb'<publication-reference\b[^>]*>.*?'
    rb'<kind>\s*([A-Z]\d?)\s*</kind>'
    rb'.*?</publication-reference>',
    re.DOTALL,
)

# Regex to extract date from <publication-reference>
_DATE_RE = re.compile(
    rb'<publication-reference\b[^>]*>.*?'
    rb'<date>\s*(\d{4,8})\s*</date>'
    rb'.*?</publication-reference>',
    re.DOTALL,
)

# Detect document type from root element
_GRANT_RE = re.compile(rb'<us-patent-grant\b')
_APP_RE = re.compile(rb'<us-patent-application\b')


def _extract_patent_info(xml_block: bytes) -> Optional[Tuple[str, str, str, Optional[int]]]:
    """
    Extract (doc_number, kind_code, doc_type, year) from a single patent XML block.

    Only parses the <publication-reference> section — fast regex extraction.
    Returns None if doc-number cannot be found.
    """
    # Determine document type (search further — DTD declarations can be long)
    header = xml_block[:2000]
    if _GRANT_RE.search(header):
        doc_type = "grant"
    elif _APP_RE.search(header):
        doc_type = "application"
    else:
        doc_type = "unknown"

    # Extract doc number
    m = _PUB_REF_RE.search(xml_block[:4096])
    if not m:
        return None
    doc_number = m.group(1).decode('ascii')

    # Extract kind code
    km = _KIND_RE.search(xml_block[:4096])
    kind_code = km.group(1).decode('ascii') if km else None

    # Extract year from date
    year = None
    dm = _DATE_RE.search(xml_block[:4096])
    if dm:
        date_str = dm.group(1).decode('ascii')
        if len(date_str) >= 4:
            try:
                year = int(date_str[:4])
            except ValueError:
                pass

    return doc_number, kind_code, doc_type, year


# ---------------------------------------------------------------------------
# Database schema
# ---------------------------------------------------------------------------

def create_database_schema(db_path: str) -> sqlite3.Connection:
    """Create the SQLite database and patent_index/bulk_files tables."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS patent_index (
            patent_id TEXT PRIMARY KEY,
            archive_file TEXT NOT NULL,
            offset INTEGER NOT NULL,
            size INTEGER NOT NULL,
            doc_type TEXT NOT NULL DEFAULT 'grant',
            kind_code TEXT,
            year INTEGER,
            record_created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Reuse the same bulk_files table as the arXiv indexer
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS bulk_files (
            file_path TEXT PRIMARY KEY,
            file_hash TEXT NOT NULL,
            last_modified REAL NOT NULL,
            processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    cursor.execute('CREATE INDEX IF NOT EXISTS idx_patent_year ON patent_index(year)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_patent_archive ON patent_index(archive_file)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_patent_doc_type ON patent_index(doc_type)')

    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# ZIP processing
# ---------------------------------------------------------------------------

@dataclass
class ZipFileResult:
    """Result from processing a single ZIP file."""
    zip_path: str
    relative_path: str
    file_hash: str
    mtime: float
    entries: List[Tuple]  # (patent_id, archive_file, offset, size, doc_type, kind_code, year)
    error: Optional[str] = None
    skipped: int = 0


def _split_xml_on_declarations(content: bytes) -> List[Tuple[int, int]]:
    """
    Split concatenated XML content on <?xml ...?> boundaries.

    Returns list of (offset, size) tuples for each document block.
    Each block starts with <?xml and ends just before the next <?xml (or EOF).
    """
    marker = b'<?xml'
    boundaries = []
    start = 0

    while True:
        pos = content.find(marker, start)
        if pos == -1:
            break
        boundaries.append(pos)
        start = pos + len(marker)

    if not boundaries:
        return []

    result = []
    for i, offset in enumerate(boundaries):
        if i + 1 < len(boundaries):
            size = boundaries[i + 1] - offset
        else:
            size = len(content) - offset
        result.append((offset, size))

    return result


def process_zip_file_worker(args: Tuple[str, str]) -> ZipFileResult:
    """
    Worker function to process a single USPTO ZIP file.
    Runs in a separate process — no database access.

    Args:
        args: (zip_path, root_dir)

    Returns:
        ZipFileResult with patent entries
    """
    zip_path, root_dir = args
    relative_path = os.path.relpath(zip_path, root_dir)
    entries = []
    skipped = 0

    try:
        # Calculate MD5 hash
        hash_md5 = hashlib.md5()
        with open(zip_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                hash_md5.update(chunk)
        file_hash = hash_md5.hexdigest()
        mtime = os.stat(zip_path).st_mtime

        # Open ZIP and read the inner XML
        with zipfile.ZipFile(zip_path, 'r') as zf:
            xml_names = [n for n in zf.namelist() if n.lower().endswith('.xml')]
            if not xml_names:
                return ZipFileResult(
                    zip_path=zip_path,
                    relative_path=relative_path,
                    file_hash=file_hash,
                    mtime=mtime,
                    entries=[],
                    error="No XML file found in ZIP",
                )

            with zf.open(xml_names[0]) as xml_file:
                content = xml_file.read()

        # Split on <?xml declarations
        blocks = _split_xml_on_declarations(content)

        for offset, size in blocks:
            block = content[offset:offset + size]
            info = _extract_patent_info(block)
            if info is None:
                skipped += 1
                continue

            doc_number, kind_code, doc_type, year = info

            entries.append((
                doc_number,       # patent_id
                relative_path,    # archive_file
                offset,           # byte offset in decompressed XML
                size,             # byte length of this patent's XML
                doc_type,         # grant or application
                kind_code,        # B2, A1, etc.
                year,             # publication year
            ))

        return ZipFileResult(
            zip_path=zip_path,
            relative_path=relative_path,
            file_hash=file_hash,
            mtime=mtime,
            entries=entries,
            skipped=skipped,
        )

    except Exception as e:
        return ZipFileResult(
            zip_path=zip_path,
            relative_path=relative_path,
            file_hash="",
            mtime=0,
            entries=[],
            error=str(e),
        )


# ---------------------------------------------------------------------------
# Main scanning logic
# ---------------------------------------------------------------------------

def get_processed_files(conn: sqlite3.Connection) -> dict:
    """Get all processed files with their hashes and mtimes."""
    cursor = conn.cursor()
    cursor.execute('SELECT file_path, file_hash, last_modified FROM bulk_files')
    return {row[0]: (row[1], row[2]) for row in cursor.fetchall()}


def _format_time(seconds: float) -> str:
    """Format seconds into human-readable time."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    elif seconds < 3600:
        return f"{seconds / 60:.1f}m"
    else:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        return f"{h}h{m:02d}m"


def _print_progress(files_done: int, files_total: int, patents: int,
                    failed: int, elapsed: float):
    """Print a single-line progress bar to stderr."""
    pct = files_done / files_total if files_total else 0
    bar_width = 30
    filled = int(bar_width * pct)
    bar = "#" * filled + "-" * (bar_width - filled)

    rate = files_done / elapsed if elapsed > 0 else 0
    remaining = files_total - files_done
    eta = remaining / rate if rate > 0 else 0

    parts = [
        f"\r[{bar}] {files_done}/{files_total}",
        f"{pct:6.1%}",
        f"{patents:,} patents",
        f"{_format_time(elapsed)} elapsed",
    ]
    if files_done < files_total and rate > 0:
        parts.append(f"~{_format_time(eta)} remaining")
    if failed:
        parts.append(f"{failed} failed")

    import sys
    sys.stderr.write(" | ".join(parts))
    sys.stderr.flush()


def scan_uspto_directory(root_dir: str, db_path: str, num_workers: int = None,
                         verbose: bool = False):
    """Scan USPTO directory for ZIP files and build the patent index."""
    if num_workers is None:
        num_workers = max(1, multiprocessing.cpu_count() - 1)

    if verbose:
        logger.info(f"Scanning USPTO directory: {root_dir}")
        logger.info(f"Database: {db_path}")
        logger.info(f"Workers: {num_workers}")
    else:
        print(f"Scanning {root_dir} -> {db_path} ({num_workers} workers)")

    conn = create_database_schema(db_path)

    try:
        root_path = Path(root_dir)
        if not root_path.exists():
            raise ValueError(f"Root directory does not exist: {root_dir}")

        processed_files = get_processed_files(conn)

        # Collect ZIP files from PTGRXML/ and APPXML/ subdirectories
        work_items = []
        skipped_count = 0

        subdirs = []
        for name in ["PTGRXML", "APPXML"]:
            subdir = root_path / name
            if subdir.exists() and subdir.is_dir():
                subdirs.append(subdir)

        # Also scan root dir directly in case ZIPs are there
        subdirs.append(root_path)

        seen_zips = set()
        for search_dir in subdirs:
            for zip_file in sorted(search_dir.glob("*.zip")):
                zip_path_str = str(zip_file)
                if zip_path_str in seen_zips:
                    continue
                seen_zips.add(zip_path_str)

                relative_path = os.path.relpath(zip_path_str, str(root_path))

                if relative_path in processed_files:
                    cached_hash, cached_mtime = processed_files[relative_path]
                    current_mtime = os.stat(zip_path_str).st_mtime
                    if current_mtime == cached_mtime:
                        skipped_count += 1
                        continue

                work_items.append((zip_path_str, str(root_path)))

        if verbose:
            if skipped_count:
                logger.info(f"Skipping {skipped_count} already-indexed files")
            logger.info(f"Indexing {len(work_items)} ZIP files")
        else:
            msg = f"{len(work_items)} files to index"
            if skipped_count:
                msg += f" ({skipped_count} already indexed, skipped)"
            print(msg)

        if not work_items:
            print("Nothing to do.")
            return

        # Process files in parallel
        import time
        total_entries = 0
        files_processed = 0
        files_failed = 0
        start_time = time.monotonic()

        with ProcessPoolExecutor(max_workers=num_workers) as executor:
            futures = {
                executor.submit(process_zip_file_worker, item): item
                for item in work_items
            }

            for future in as_completed(futures):
                result = future.result()

                if result.error:
                    logger.error(f"FAILED: {result.relative_path}: {result.error}")
                    files_failed += 1
                    if not verbose:
                        _print_progress(
                            files_processed + files_failed, len(work_items),
                            total_entries, files_failed,
                            time.monotonic() - start_time,
                        )
                    continue

                # Check if hash changed
                if result.relative_path in processed_files:
                    cached_hash, _ = processed_files[result.relative_path]
                    if cached_hash == result.file_hash:
                        if verbose:
                            logger.debug(f"Skipping {result.zip_path} - hash unchanged")
                        continue

                # Batch insert entries
                cursor = conn.cursor()
                cursor.executemany('''
                    INSERT OR REPLACE INTO patent_index
                    (patent_id, archive_file, offset, size, doc_type, kind_code, year)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', result.entries)

                cursor.execute('''
                    INSERT OR REPLACE INTO bulk_files (file_path, file_hash, last_modified)
                    VALUES (?, ?, ?)
                ''', (result.relative_path, result.file_hash, result.mtime))

                conn.commit()

                files_processed += 1
                total_entries += len(result.entries)

                if verbose:
                    logger.info(
                        f"Indexed {result.relative_path}: "
                        f"{len(result.entries)} patents"
                        f"{f' ({result.skipped} skipped)' if result.skipped else ''}"
                    )
                else:
                    _print_progress(
                        files_processed + files_failed, len(work_items),
                        total_entries, files_failed,
                        time.monotonic() - start_time,
                    )

        # Clear progress line
        if not verbose:
            import sys
            sys.stderr.write("\n")
            sys.stderr.flush()

        # Print summary
        elapsed = time.monotonic() - start_time
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM patent_index')
        total_patents = cursor.fetchone()[0]

        print(f"\nDone in {_format_time(elapsed)}. "
              f"{files_processed} files indexed, "
              f"{total_entries:,} patents added, "
              f"{total_patents:,} total in database."
              f"{f' {files_failed} failed.' if files_failed else ''}")

    finally:
        conn.close()


def index_single_file(file_input: str, root_dir: str, db_path: str):
    """
    Index a single ZIP file.

    Args:
        file_input: Path to ZIP file (absolute or relative to root_dir subdirectories)
        root_dir: Root directory containing PTGRXML/APPXML subdirectories
        db_path: Path to SQLite database
    """
    conn = create_database_schema(db_path)

    try:
        # Resolve the file path
        if os.path.isabs(file_input) and os.path.exists(file_input):
            zip_path = file_input
        else:
            # Search in known subdirectories
            filename = os.path.basename(file_input)
            zip_path = None
            for subdir in ["PTGRXML", "APPXML", ""]:
                candidate = os.path.join(root_dir, subdir, filename)
                if os.path.exists(candidate):
                    zip_path = candidate
                    break

            if zip_path is None:
                raise ValueError(f"ZIP file not found: {file_input}")

        print(f"Indexing {zip_path} ...")

        result = process_zip_file_worker((zip_path, root_dir))

        if result.error:
            raise RuntimeError(f"Failed to process {zip_path}: {result.error}")

        # Insert entries
        cursor = conn.cursor()
        cursor.executemany('''
            INSERT OR REPLACE INTO patent_index
            (patent_id, archive_file, offset, size, doc_type, kind_code, year)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', result.entries)

        # Mark as processed
        cursor.execute('''
            INSERT OR REPLACE INTO bulk_files (file_path, file_hash, last_modified)
            VALUES (?, ?, ?)
        ''', (result.relative_path, result.file_hash, result.mtime))

        conn.commit()
        print(f"Indexed {len(result.entries):,} patents.")

    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="Build SQLite index for USPTO patent bulk ZIP archives"
    )
    parser.add_argument(
        "root_dir",
        help="Root directory containing USPTO bulk files (e.g., /data/uspto)"
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="Path to SQLite database file (default: uspto_manifest.sqlite3 in index/ directory)"
    )
    parser.add_argument(
        "--single-file",
        help="Index a single ZIP file (absolute path or filename)"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging"
    )
    parser.add_argument(
        "--workers", "-w",
        type=int,
        default=None,
        help="Number of parallel workers (default: CPU count - 1)"
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    else:
        logging.getLogger().setLevel(logging.WARNING)

    # Resolve database path
    db_path = args.db_path
    if db_path is None:
        script_dir = Path(__file__).parent.resolve()
        default_path = script_dir / "uspto_manifest.sqlite3"
        db_path = str(default_path)

    try:
        if args.single_file:
            index_single_file(args.single_file, args.root_dir, db_path)
        else:
            scan_uspto_directory(args.root_dir, db_path, num_workers=args.workers,
                                verbose=args.verbose)
    except Exception as e:
        logger.error(f"Failed to build index: {e}")
        return 1

    return 0


if __name__ == "__main__":
    exit(main())
