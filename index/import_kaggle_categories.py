#!/usr/bin/env python3
"""
Import paper categories from the Kaggle arXiv metadata dataset.

This script reads the arxiv-metadata-oai-snapshot.json file (JSONL format)
and populates the categories column in the paper_index table.

Download the dataset from:
https://www.kaggle.com/datasets/Cornell-University/arxiv

Usage:
    python import_kaggle_categories.py metadata.json.zip db_path
    python import_kaggle_categories.py metadata.json db_path  # uncompressed
"""

import argparse
import json
import sqlite3
import sys
import zipfile
from pathlib import Path


def add_categories_column(conn: sqlite3.Connection):
    """Add categories column to paper_index if it doesn't exist."""
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(paper_index)")
    columns = [row[1] for row in cursor.fetchall()]

    if 'categories' not in columns:
        print("Adding 'categories' column to paper_index table...")
        cursor.execute("ALTER TABLE paper_index ADD COLUMN categories TEXT")
        conn.commit()
        print("Column added.")

    # Create index for category searches
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_categories ON paper_index(categories)")
    conn.commit()


def normalize_paper_id(paper_id: str) -> str:
    """
    Normalize Kaggle paper ID to match database format.

    Kaggle uses: 0704.0001, astro-ph/0001001
    Database uses: 0704.0001, astro-ph0001001 (no slash)
    """
    return paper_id.replace('/', '')


def open_metadata_file(path: str):
    """Open metadata file, handling both .zip and plain .json files."""
    if path.endswith('.zip'):
        zf = zipfile.ZipFile(path, 'r')
        # Find the json file inside
        json_files = [n for n in zf.namelist() if n.endswith('.json')]
        if not json_files:
            raise ValueError("No .json file found in zip archive")
        return zf.open(json_files[0])
    else:
        return open(path, 'rb')


def import_categories(metadata_path: str, db_path: str, batch_size: int = 10000):
    """
    Import categories from Kaggle metadata into SQLite database.

    Args:
        metadata_path: Path to arxiv-metadata-oai-snapshot.json or .zip
        db_path: Path to SQLite database
        batch_size: Number of updates per transaction
    """
    conn = sqlite3.connect(db_path)

    try:
        # Ensure categories column exists
        add_categories_column(conn)

        cursor = conn.cursor()

        # Get set of paper IDs in our database for fast lookup
        print("Loading paper IDs from database...")
        cursor.execute("SELECT paper_id FROM paper_index")
        db_paper_ids = set(row[0] for row in cursor.fetchall())
        print(f"Found {len(db_paper_ids):,} papers in database")

        # Process metadata file
        print(f"Reading metadata from {metadata_path}...")

        total_processed = 0
        total_matched = 0
        total_updated = 0
        batch = []

        with open_metadata_file(metadata_path) as f:
            for line_num, line in enumerate(f, 1):
                try:
                    if isinstance(line, bytes):
                        line = line.decode('utf-8')

                    data = json.loads(line)
                    paper_id = data.get('id', '')
                    categories = data.get('categories', '')

                    if not paper_id or not categories:
                        continue

                    # Normalize the paper ID
                    normalized_id = normalize_paper_id(paper_id)

                    # Handle categories - can be list or space-separated string
                    if isinstance(categories, list):
                        categories_str = ' '.join(categories)
                    else:
                        categories_str = categories

                    total_processed += 1

                    # Check if paper exists in our database
                    if normalized_id in db_paper_ids:
                        total_matched += 1
                        batch.append((categories_str, normalized_id))

                        # Execute batch update
                        if len(batch) >= batch_size:
                            cursor.executemany(
                                "UPDATE paper_index SET categories = ? WHERE paper_id = ?",
                                batch
                            )
                            total_updated += cursor.rowcount
                            conn.commit()
                            batch = []
                            print(f"  Processed {total_processed:,} / Matched {total_matched:,} / Updated {total_updated:,}", end='\r')

                except json.JSONDecodeError as e:
                    print(f"\nWarning: Invalid JSON on line {line_num}: {e}")
                    continue

        # Final batch
        if batch:
            cursor.executemany(
                "UPDATE paper_index SET categories = ? WHERE paper_id = ?",
                batch
            )
            total_updated += cursor.rowcount
            conn.commit()

        print(f"\n\nImport complete!")
        print(f"  Total in metadata: {total_processed:,}")
        print(f"  Matched in database: {total_matched:,}")
        print(f"  Updated: {total_updated:,}")

        # Show category statistics
        cursor.execute("""
            SELECT COUNT(*) FROM paper_index
            WHERE categories IS NOT NULL AND categories != ''
        """)
        with_cats = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM paper_index")
        total = cursor.fetchone()[0]

        print(f"\nDatabase now has {with_cats:,} / {total:,} papers with categories ({100*with_cats/total:.1f}%)")

    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="Import categories from Kaggle arXiv metadata dataset"
    )
    parser.add_argument(
        "metadata_path",
        help="Path to arxiv-metadata-oai-snapshot.json or .zip file"
    )
    parser.add_argument(
        "db_path",
        help="Path to SQLite database"
    )
    parser.add_argument(
        "--batch-size", "-b",
        type=int,
        default=10000,
        help="Batch size for database updates (default: 10000)"
    )

    args = parser.parse_args()

    if not Path(args.metadata_path).exists():
        print(f"Error: Metadata file not found: {args.metadata_path}")
        return 1

    if not Path(args.db_path).exists():
        print(f"Error: Database not found: {args.db_path}")
        return 1

    import_categories(args.metadata_path, args.db_path, args.batch_size)
    return 0


if __name__ == "__main__":
    sys.exit(main())
