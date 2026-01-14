#!/usr/bin/env python3
"""
Import paper metadata from the Kaggle arXiv metadata dataset.

This script reads the arxiv-metadata-oai-snapshot.json file (JSONL format)
and populates metadata columns in the paper_index table.

Fields imported:
- categories: Paper categories (e.g., "hep-ph astro-ph.CO")
- title: Paper title
- authors: Author list
- abstract: Paper abstract
- doi: Digital Object Identifier
- journal_ref: Journal reference
- comments: Author comments (e.g., "37 pages, 15 figures")
- submitter: Who submitted the paper
- report_no: Report number
- versions: Available versions (e.g., "v1 v2 v3")

Download the dataset from:
https://www.kaggle.com/datasets/Cornell-University/arxiv

Usage:
    python import_kaggle_metadata.py metadata.json.zip db_path
    python import_kaggle_metadata.py metadata.json db_path  # uncompressed
"""

import argparse
import json
import sqlite3
import sys
import zipfile
from pathlib import Path


# Columns to add and their SQLite types
METADATA_COLUMNS = {
    'categories': 'TEXT',
    'title': 'TEXT',
    'authors': 'TEXT',
    'abstract': 'TEXT',
    'doi': 'TEXT',
    'journal_ref': 'TEXT',
    'comments': 'TEXT',
    'submitter': 'TEXT',
    'report_no': 'TEXT',
    'versions': 'TEXT',
}

# Mapping from Kaggle field names to database column names
FIELD_MAPPING = {
    'categories': 'categories',
    'title': 'title',
    'authors': 'authors',
    'abstract': 'abstract',
    'doi': 'doi',
    'journal-ref': 'journal_ref',
    'comments': 'comments',
    'submitter': 'submitter',
    'report-no': 'report_no',
    'versions': 'versions',
}


def add_metadata_columns(conn: sqlite3.Connection) -> list:
    """Add metadata columns to paper_index if they don't exist."""
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(paper_index)")
    existing_columns = set(row[1] for row in cursor.fetchall())

    added = []
    for col_name, col_type in METADATA_COLUMNS.items():
        if col_name not in existing_columns:
            print(f"Adding '{col_name}' column...")
            cursor.execute(f"ALTER TABLE paper_index ADD COLUMN {col_name} {col_type}")
            added.append(col_name)

    if added:
        conn.commit()
        print(f"Added {len(added)} new columns: {', '.join(added)}")

    # Create index for category searches
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_categories ON paper_index(categories)")
    # Create index for DOI lookups
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_doi ON paper_index(doi)")
    conn.commit()

    return added


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


def extract_field(data: dict, kaggle_field: str) -> str:
    """Extract a field from metadata, converting lists to space-separated strings."""
    value = data.get(kaggle_field)

    if value is None:
        return None

    if isinstance(value, list):
        return ' '.join(str(v) for v in value)

    return str(value) if value else None


def import_metadata(metadata_path: str, db_path: str, batch_size: int = 5000):
    """
    Import metadata from Kaggle dataset into SQLite database.

    Args:
        metadata_path: Path to arxiv-metadata-oai-snapshot.json or .zip
        db_path: Path to SQLite database
        batch_size: Number of updates per transaction
    """
    conn = sqlite3.connect(db_path)

    try:
        # Ensure metadata columns exist
        add_metadata_columns(conn)

        cursor = conn.cursor()

        # Get set of paper IDs in our database for fast lookup
        print("Loading paper IDs from database...")
        cursor.execute("SELECT paper_id FROM paper_index")
        db_paper_ids = set(row[0] for row in cursor.fetchall())
        print(f"Found {len(db_paper_ids):,} papers in database")

        # Build the UPDATE statement with all columns
        db_columns = list(METADATA_COLUMNS.keys())
        set_clause = ', '.join(f'{col} = ?' for col in db_columns)
        update_sql = f"UPDATE paper_index SET {set_clause} WHERE paper_id = ?"

        # Process metadata file
        print(f"Reading metadata from {metadata_path}...")
        print(f"Importing fields: {', '.join(db_columns)}")

        total_processed = 0
        total_matched = 0
        batch = []

        with open_metadata_file(metadata_path) as f:
            for line_num, line in enumerate(f, 1):
                try:
                    if isinstance(line, bytes):
                        line = line.decode('utf-8')

                    data = json.loads(line)
                    paper_id = data.get('id', '')

                    if not paper_id:
                        continue

                    # Normalize the paper ID
                    normalized_id = normalize_paper_id(paper_id)
                    total_processed += 1

                    # Check if paper exists in our database
                    if normalized_id in db_paper_ids:
                        total_matched += 1

                        # Extract all fields
                        row_values = []
                        for db_col in db_columns:
                            # Find corresponding Kaggle field
                            kaggle_field = next(
                                (kf for kf, dc in FIELD_MAPPING.items() if dc == db_col),
                                db_col
                            )
                            value = extract_field(data, kaggle_field)
                            row_values.append(value)

                        # Add paper_id for WHERE clause
                        row_values.append(normalized_id)
                        batch.append(tuple(row_values))

                        # Execute batch update
                        if len(batch) >= batch_size:
                            cursor.executemany(update_sql, batch)
                            conn.commit()
                            batch = []
                            print(f"  Processed {total_processed:,} / Matched {total_matched:,}", end='\r')

                except json.JSONDecodeError as e:
                    print(f"\nWarning: Invalid JSON on line {line_num}: {e}")
                    continue

        # Final batch
        if batch:
            cursor.executemany(update_sql, batch)
            conn.commit()

        print(f"\n\nImport complete!")
        print(f"  Total in metadata file: {total_processed:,}")
        print(f"  Matched in database: {total_matched:,}")

        # Show statistics
        print("\nField coverage in database:")
        for col in db_columns:
            cursor.execute(f"""
                SELECT COUNT(*) FROM paper_index
                WHERE {col} IS NOT NULL AND {col} != ''
            """)
            count = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM paper_index")
            total = cursor.fetchone()[0]
            pct = 100 * count / total if total > 0 else 0
            print(f"  {col}: {count:,} / {total:,} ({pct:.1f}%)")

    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="Import metadata from Kaggle arXiv dataset"
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
        default=5000,
        help="Batch size for database updates (default: 5000)"
    )

    args = parser.parse_args()

    if not Path(args.metadata_path).exists():
        print(f"Error: Metadata file not found: {args.metadata_path}")
        return 1

    if not Path(args.db_path).exists():
        print(f"Error: Database not found: {args.db_path}")
        return 1

    import_metadata(args.metadata_path, args.db_path, args.batch_size)
    return 0


if __name__ == "__main__":
    sys.exit(main())
