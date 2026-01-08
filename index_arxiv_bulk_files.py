#!/usr/bin/env python3
"""
Script to build SQLite index for arXiv paper archive.

This script scans the arXiv bulk tar files and creates an SQLite index
that allows efficient retrieval of individual papers without extracting
the entire bulk files.
"""

import argparse
import sqlite3
import os
import tarfile
import hashlib
from pathlib import Path
from typing import Set, Tuple, Optional
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def create_database_schema(db_path: str) -> sqlite3.Connection:
    """Create the SQLite database and tables if they don't exist."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Create paper_index table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS paper_index (
            paper_id TEXT PRIMARY KEY,
            archive_file TEXT NOT NULL,
            offset INTEGER NOT NULL,
            size INTEGER NOT NULL,
            file_type TEXT NOT NULL,
            year INTEGER NOT NULL,
            record_created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Create bulk_files table to track which files have been processed
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS bulk_files (
            file_path TEXT PRIMARY KEY,
            file_hash TEXT NOT NULL,
            last_modified REAL NOT NULL,
            processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Create indices for better performance
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_paper_year ON paper_index(year)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_archive_file ON paper_index(archive_file)')
    
    conn.commit()
    return conn


def get_file_hash(file_path: str) -> str:
    """Calculate MD5 hash of a file efficiently."""
    hash_md5 = hashlib.md5()
    with open(file_path, "rb") as f:
        # Read in chunks to handle large files
        for chunk in iter(lambda: f.read(4096), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()


def is_file_processed(conn: sqlite3.Connection, file_path: str, root_dir: str) -> bool:
    """Check if a bulk tar file has already been processed."""
    cursor = conn.cursor()
    
    # Get file stats
    stat = os.stat(file_path)
    file_hash = get_file_hash(file_path)
    
    # Use relative path for database lookup
    relative_path = os.path.relpath(file_path, root_dir)
    
    cursor.execute(
        'SELECT file_hash, last_modified FROM bulk_files WHERE file_path = ?',
        (relative_path,)
    )
    result = cursor.fetchone()
    
    if result is None:
        return False
    
    stored_hash, stored_mtime = result
    return stored_hash == file_hash and stored_mtime == stat.st_mtime


def mark_file_processed(conn: sqlite3.Connection, file_path: str, root_dir: str):
    """Mark a bulk tar file as processed."""
    stat = os.stat(file_path)
    file_hash = get_file_hash(file_path)
    
    # Store relative path in database
    relative_path = os.path.relpath(file_path, root_dir)
    
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO bulk_files (file_path, file_hash, last_modified)
        VALUES (?, ?, ?)
    ''', (relative_path, file_hash, stat.st_mtime))
    conn.commit()


def extract_paper_id(tar_entry_name: str) -> Optional[str]:
    """Extract paper ID from tar entry name."""
    # Remove any directory prefixes and file extensions
    basename = os.path.basename(tar_entry_name)
    
    # Remove common extensions
    for ext in ['.gz', '.pdf', '.tar', '.zip']:
        if basename.endswith(ext):
            basename = basename[:-len(ext)]
    
    # arXiv paper IDs typically follow patterns like:
    # - YYMM.NNNN (old format)
    # - subject-class/YYMMnnn (old format with subject class)
    # - YYYY.NNNN (new format)
    if '/' in basename:
        # Handle old format with subject class
        return basename
    elif '.' in basename and (len(basename.split('.')[0]) in [2, 4]):
        # Handle both old (YYMM.NNNN) and new (YYYY.NNNN) formats
        return basename
    
    return basename  # Return as-is if no clear pattern matches


def determine_file_type(tar_entry_name: str) -> str:
    """Determine file type based on the tar entry name."""
    if tar_entry_name.endswith('.pdf'):
        return 'pdf'
    elif tar_entry_name.endswith('.gz'):
        return 'gzip'
    elif tar_entry_name.endswith('.tar'):
        return 'tar'
    else:
        return 'unknown'


def index_tar_file(conn: sqlite3.Connection, tar_path: str, year: int, root_dir: str):
    """Index a single tar file and add entries to the database."""
    logger.info(f"Indexing tar file: {tar_path}")
    
    try:
        with open(tar_path, 'rb') as f:
            # Use tarfile to read the tar without extracting
            with tarfile.open(fileobj=f, mode='r|') as tar:
                cursor = conn.cursor()
                entries_added = 0
                
                for member in tar:
                    if member.isfile():
                        paper_id = extract_paper_id(member.name)
                        if paper_id:
                            file_type = determine_file_type(member.name)
                            # Store relative path from root directory
                            relative_path = os.path.relpath(tar_path, root_dir)
                            
                            # Insert into database
                            try:
                                cursor.execute('''
                                    INSERT OR REPLACE INTO paper_index 
                                    (paper_id, archive_file, offset, size, file_type, year)
                                    VALUES (?, ?, ?, ?, ?, ?)
                                ''', (
                                    paper_id,
                                    relative_path,
                                    member.offset_data,
                                    member.size,
                                    file_type,
                                    year
                                ))
                                entries_added += 1
                            except sqlite3.Error as e:
                                logger.warning(f"Error inserting {paper_id}: {e}")
                
                conn.commit()
                logger.info(f"Added {entries_added} entries from {tar_path}")
                
    except Exception as e:
        logger.error(f"Error processing {tar_path}: {e}")
        raise


def scan_arxiv_directory(root_dir: str, db_path: str):
    """Scan the arXiv directory structure and build the index."""
    logger.info(f"Scanning arXiv directory: {root_dir}")
    
    conn = create_database_schema(db_path)
    
    try:
        root_path = Path(root_dir)
        if not root_path.exists():
            raise ValueError(f"Root directory does not exist: {root_dir}")
        
        # Iterate through year directories
        for year_dir in sorted(root_path.iterdir()):
            if year_dir.is_dir() and year_dir.name.isdigit():
                year = int(year_dir.name)
                logger.info(f"Processing year: {year}")
                
                # Find all tar files in the year directory
                tar_files = list(year_dir.glob("*.tar"))
                logger.info(f"Found {len(tar_files)} tar files for year {year}")
                
                for tar_file in sorted(tar_files):
                    tar_path_str = str(tar_file)
                    
                    # Check if file has already been processed
                    if is_file_processed(conn, tar_path_str, str(root_path)):
                        logger.info(f"Skipping already processed file: {tar_file.name}")
                        continue
                    
                    try:
                        index_tar_file(conn, tar_path_str, year, str(root_path))
                        mark_file_processed(conn, tar_path_str, str(root_path))
                    except Exception as e:
                        logger.error(f"Failed to process {tar_file}: {e}")
                        continue
        
        # Print summary statistics
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM paper_index')
        total_papers = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(DISTINCT year) FROM paper_index')
        total_years = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(DISTINCT archive_file) FROM paper_index')
        total_archives = cursor.fetchone()[0]
        
        logger.info(f"Indexing complete!")
        logger.info(f"Total papers indexed: {total_papers}")
        logger.info(f"Years covered: {total_years}")
        logger.info(f"Archive files processed: {total_archives}")
        
    finally:
        conn.close()


def extract_year_from_filename(filename: str) -> int:
    """Extract year from arXiv bulk filename formats."""
    import re
    
    # arXiv bulk file formats: arXiv_pdf_yymm_nnn.tar or arXiv_src_yymm_nnn.tar
    arxiv_match = re.search(r'arXiv_(pdf|src)_(\d{2})(\d{2})_\d{3}\.tar', filename)
    if arxiv_match:
        yy = int(arxiv_match.group(2))
        # Convert 2-digit year to 4-digit year
        # arXiv started in 1991, so years 91-99 are 1991-1999, years 00-90 are 2000-2090
        if yy >= 91:
            return 1900 + yy
        else:
            return 2000 + yy
    
    raise ValueError(f"Cannot extract year from filename: {filename} (expected format: arXiv_pdf_yymm_nnn.tar or arXiv_src_yymm_nnn.tar)")


def resolve_tar_file_path(file_input: str, root_dir: str) -> str:
    """
    Resolve tar file path from either absolute path or filename.
    If filename only, look for it in the appropriate year directory.
    """
    # Check if it's an absolute path
    if os.path.isabs(file_input) and os.path.exists(file_input):
        if not file_input.endswith('.tar'):
            raise ValueError(f"File is not a tar archive: {file_input}")
        return file_input
    
    # Treat as filename - extract year and look for file
    filename = os.path.basename(file_input)
    if not filename.endswith('.tar'):
        raise ValueError(f"File is not a tar archive: {filename}")
    
    # Extract year from filename
    year = extract_year_from_filename(filename)
    
    # Construct expected path
    expected_path = os.path.join(root_dir, str(year), filename)
    
    if not os.path.exists(expected_path):
        raise ValueError(f"Tar file not found: {expected_path}")
    
    return expected_path


def index_single_file(file_input: str, root_dir: str, db_path: str):
    """
    Index a single tar file.
    
    Args:
        file_input: Either absolute path to tar file or just the filename
        root_dir: Root directory containing year subdirectories
        db_path: Path to SQLite database
    """
    logger.info(f"Resolving file input: {file_input}")
    
    conn = create_database_schema(db_path)
    
    try:
        # Resolve the actual tar file path
        tar_file_path = resolve_tar_file_path(file_input, root_dir)
        logger.info(f"Resolved to: {tar_file_path}")
        
        # Extract year from the resolved path
        # Try directory structure first
        relative_path = os.path.relpath(tar_file_path, root_dir)
        path_parts = relative_path.split(os.sep)
        
        if len(path_parts) >= 2 and path_parts[0].isdigit():
            year = int(path_parts[0])
        else:
            # Fall back to filename extraction
            filename = os.path.basename(tar_file_path)
            year = extract_year_from_filename(filename)
        
        # Check if file has already been processed
        if is_file_processed(conn, tar_file_path, root_dir):
            logger.info(f"File already processed: {tar_file_path}")
            return
        
        index_tar_file(conn, tar_file_path, year, root_dir)
        mark_file_processed(conn, tar_file_path, root_dir)
        
        logger.info(f"Successfully indexed: {tar_file_path}")
        
    finally:
        conn.close()


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Build SQLite index for arXiv paper archive"
    )
    parser.add_argument(
        "root_dir",
        help="Root directory containing arXiv bulk files (e.g., /raid1/arXiv/arXiv)"
    )
    parser.add_argument(
        "--db-path",
        default="arxiv_index.db",
        help="Path to SQLite database file (default: arxiv_index.db)"
    )
    parser.add_argument(
        "--single-file",
        help="Index a single tar file (absolute path or filename). If filename only, will look in appropriate year directory."
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging"
    )
    
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    try:
        if args.single_file:
            index_single_file(args.single_file, args.root_dir, args.db_path)
        else:
            scan_arxiv_directory(args.root_dir, args.db_path)
    except Exception as e:
        logger.error(f"Failed to build index: {e}")
        return 1
    
    return 0


if __name__ == "__main__":
    exit(main())