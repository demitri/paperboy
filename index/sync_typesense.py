#!/usr/bin/env python3
"""
Sync paper metadata from SQLite database to Typesense search engine.

This script creates/updates the Typesense collection and indexes all papers
with metadata (title, authors, abstract, categories) for full-text search.

Usage:
    python sync_typesense.py --db-path /path/to/db.sqlite3

    # With custom Typesense settings:
    python sync_typesense.py --db-path /path/to/db.sqlite3 \
        --host localhost --port 8108 --api-key your-key

    # Recreate collection from scratch:
    python sync_typesense.py --db-path /path/to/db.sqlite3 --recreate
"""

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional

import typesense
from typesense.exceptions import ObjectNotFound


# Collection schema for papers
PAPERS_SCHEMA = {
    "name": "papers",
    "fields": [
        {"name": "paper_id", "type": "string"},
        {"name": "title", "type": "string"},
        {"name": "authors", "type": "string"},
        {"name": "abstract", "type": "string"},
        {"name": "categories", "type": "string[]", "facet": True},
        {"name": "primary_category", "type": "string", "facet": True},
        {"name": "year", "type": "int32", "facet": True},
        {"name": "doi", "type": "string", "optional": True},
        {"name": "journal_ref", "type": "string", "optional": True},
        {"name": "file_type", "type": "string", "facet": True},
    ],
    "default_sorting_field": "year",
    "enable_nested_fields": False,
}


def create_typesense_client(
    host: str = "localhost",
    port: int = 8108,
    protocol: str = "http",
    api_key: str = "paperboy-search-key"
) -> typesense.Client:
    """Create and return a Typesense client."""
    return typesense.Client({
        "nodes": [{
            "host": host,
            "port": str(port),
            "protocol": protocol,
        }],
        "api_key": api_key,
        "connection_timeout_seconds": 10,
    })


def ensure_collection(client: typesense.Client, recreate: bool = False) -> None:
    """Ensure the papers collection exists with correct schema."""
    collection_name = PAPERS_SCHEMA["name"]

    try:
        existing = client.collections[collection_name].retrieve()
        print(f"Collection '{collection_name}' exists with {existing['num_documents']} documents")

        if recreate:
            print(f"Recreating collection '{collection_name}'...")
            client.collections[collection_name].delete()
            client.collections.create(PAPERS_SCHEMA)
            print(f"Collection recreated successfully")

    except ObjectNotFound:
        print(f"Creating collection '{collection_name}'...")
        client.collections.create(PAPERS_SCHEMA)
        print(f"Collection created successfully")


def get_papers_with_metadata(conn: sqlite3.Connection, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Fetch papers with metadata from SQLite database."""
    cursor = conn.cursor()

    query = """
        SELECT paper_id, title, authors, abstract, categories, year, doi, journal_ref, file_type
        FROM paper_index
        WHERE title IS NOT NULL AND title != ''
    """
    if limit:
        query += f" LIMIT {limit}"

    cursor.execute(query)

    papers = []
    for row in cursor.fetchall():
        paper_id, title, authors, abstract, categories, year, doi, journal_ref, file_type = row

        # Parse categories into list
        cat_list = categories.split() if categories else []
        primary_category = cat_list[0] if cat_list else "unknown"

        # Build document
        doc = {
            "id": paper_id,  # Typesense document ID
            "paper_id": paper_id,
            "title": title or "",
            "authors": authors or "",
            "abstract": abstract or "",
            "categories": cat_list,
            "primary_category": primary_category,
            "year": year or 0,
            "file_type": file_type or "unknown",
        }

        # Optional fields
        if doi:
            doc["doi"] = doi
        if journal_ref:
            doc["journal_ref"] = journal_ref

        papers.append(doc)

    return papers


def index_papers(
    client: typesense.Client,
    papers: List[Dict[str, Any]],
    batch_size: int = 1000
) -> Dict[str, int]:
    """Index papers to Typesense in batches."""
    collection_name = PAPERS_SCHEMA["name"]
    total = len(papers)
    indexed = 0
    errors = 0

    print(f"Indexing {total:,} papers...")

    for i in range(0, total, batch_size):
        batch = papers[i:i + batch_size]

        try:
            # Use import for bulk indexing
            results = client.collections[collection_name].documents.import_(
                batch,
                {"action": "upsert"}
            )

            # Count successes and failures
            for result in results:
                if result.get("success", False):
                    indexed += 1
                else:
                    errors += 1
                    if errors <= 5:  # Only show first 5 errors
                        print(f"  Error: {result.get('error', 'unknown')}")

        except Exception as e:
            print(f"  Batch error: {e}")
            errors += len(batch)

        # Progress update
        progress = min(i + batch_size, total)
        print(f"  Progress: {progress:,}/{total:,} ({100*progress/total:.1f}%)", end="\r")

    print(f"\n\nIndexing complete!")
    print(f"  Successfully indexed: {indexed:,}")
    print(f"  Errors: {errors:,}")

    return {"indexed": indexed, "errors": errors}


def get_collection_stats(client: typesense.Client) -> Dict[str, Any]:
    """Get current collection statistics."""
    collection_name = PAPERS_SCHEMA["name"]
    try:
        info = client.collections[collection_name].retrieve()
        return {
            "name": info["name"],
            "num_documents": info["num_documents"],
            "fields": len(info["fields"]),
        }
    except ObjectNotFound:
        return {"error": "Collection not found"}


def main():
    parser = argparse.ArgumentParser(
        description="Sync paper metadata from SQLite to Typesense"
    )
    parser.add_argument(
        "--db-path",
        required=True,
        help="Path to SQLite database"
    )
    parser.add_argument(
        "--host",
        default="localhost",
        help="Typesense host (default: localhost)"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8108,
        help="Typesense port (default: 8108)"
    )
    parser.add_argument(
        "--protocol",
        default="http",
        choices=["http", "https"],
        help="Typesense protocol (default: http)"
    )
    parser.add_argument(
        "--api-key",
        default="paperboy-search-key",
        help="Typesense API key"
    )
    parser.add_argument(
        "--recreate",
        action="store_true",
        help="Delete and recreate the collection"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=1000,
        help="Batch size for indexing (default: 1000)"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of papers to index (for testing)"
    )
    parser.add_argument(
        "--stats-only",
        action="store_true",
        help="Only show collection statistics"
    )

    args = parser.parse_args()

    # Validate database path
    if not Path(args.db_path).exists():
        print(f"Error: Database not found: {args.db_path}")
        return 1

    # Create Typesense client
    print(f"Connecting to Typesense at {args.protocol}://{args.host}:{args.port}")
    try:
        client = create_typesense_client(
            host=args.host,
            port=args.port,
            protocol=args.protocol,
            api_key=args.api_key
        )
        # Test connection by listing collections
        client.collections.retrieve()
        print("Connected successfully")
    except Exception as e:
        print(f"Error connecting to Typesense: {e}")
        return 1

    # Stats only mode
    if args.stats_only:
        stats = get_collection_stats(client)
        print(f"\nCollection stats: {stats}")
        return 0

    # Ensure collection exists
    ensure_collection(client, recreate=args.recreate)

    # Connect to SQLite
    print(f"\nReading papers from {args.db_path}")
    conn = sqlite3.connect(args.db_path)

    try:
        # Fetch papers with metadata
        papers = get_papers_with_metadata(conn, limit=args.limit)
        print(f"Found {len(papers):,} papers with metadata")

        if not papers:
            print("No papers with metadata found. Run import_kaggle_metadata.py first.")
            return 1

        # Index papers
        results = index_papers(client, papers, batch_size=args.batch_size)

        # Show final stats
        stats = get_collection_stats(client)
        print(f"\nFinal collection stats: {stats}")

    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
