#!/usr/bin/env python3
"""
Test script to extract arXiv papers using the SQLite index.

This script demonstrates the paper extraction functionality by looking up
a paper ID in the index database and extracting the corresponding file
from the bulk tar archives.
"""

import argparse
import sqlite3
import os
import gzip
import sys
from pathlib import Path
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class PaperExtractor:
    """Extract papers from arXiv bulk files using SQLite index."""
    
    def __init__(self, db_path: str, root_dir: str):
        """
        Initialize the paper extractor.
        
        Args:
            db_path: Path to SQLite index database
            root_dir: Root directory containing arXiv bulk files
        """
        self.db_path = db_path
        self.root_dir = root_dir
        
        # Validate inputs
        if not os.path.exists(db_path):
            raise ValueError(f"Database file not found: {db_path}")
        if not os.path.exists(root_dir):
            raise ValueError(f"Root directory not found: {root_dir}")
        
        # Connect to database
        self.conn = sqlite3.connect(db_path)
    
    def __del__(self):
        """Close database connection."""
        if hasattr(self, 'conn'):
            self.conn.close()
    
    def find_paper(self, paper_id: str) -> dict:
        """
        Look up paper information in the index.
        
        Args:
            paper_id: The arXiv paper ID to find
            
        Returns:
            Dictionary with paper information or None if not found
        """
        cursor = self.conn.cursor()
        cursor.execute(
            'SELECT archive_file, offset, size, file_type, year FROM paper_index WHERE paper_id = ?',
            (paper_id,)
        )
        result = cursor.fetchone()
        
        if result is None:
            return None
        
        return {
            'paper_id': paper_id,
            'archive_file': result[0],
            'offset': result[1],
            'size': result[2],
            'file_type': result[3],
            'year': result[4]
        }
    
    def extract_paper_data(self, paper_info: dict) -> bytes:
        """
        Extract paper data from the bulk tar file.
        
        Args:
            paper_info: Paper information from find_paper()
            
        Returns:
            Raw bytes of the paper file
        """
        # Construct full path to tar file
        tar_file_path = os.path.join(self.root_dir, paper_info['archive_file'])
        
        if not os.path.exists(tar_file_path):
            raise FileNotFoundError(f"Tar file not found: {tar_file_path}")
        
        logger.info(f"Extracting from: {tar_file_path}")
        logger.info(f"Offset: {paper_info['offset']}, Size: {paper_info['size']}")
        
        # Read the specific bytes from the tar file
        with open(tar_file_path, 'rb') as f:
            f.seek(paper_info['offset'])
            file_data = f.read(paper_info['size'])
        
        # Return the raw file data (no decompression)
        # The files in the tar are already in their final format (.gz or .pdf)
        logger.info(f"Extracted {len(file_data)} bytes")
        return file_data
    
    def extract_to_file(self, paper_id: str, output_dir: str = ".") -> str:
        """
        Extract a paper and save it to a file.
        
        Args:
            paper_id: The arXiv paper ID to extract
            output_dir: Directory to save the extracted file
            
        Returns:
            Path to the extracted file
        """
        # Find paper in index
        paper_info = self.find_paper(paper_id)
        if paper_info is None:
            raise ValueError(f"Paper not found in index: {paper_id}")
        
        logger.info(f"Found paper {paper_id}:")
        logger.info(f"  Archive: {paper_info['archive_file']}")
        logger.info(f"  Type: {paper_info['file_type']}")
        logger.info(f"  Year: {paper_info['year']}")
        
        # Extract paper data
        paper_data = self.extract_paper_data(paper_info)
        
        # Determine output filename and extension
        if paper_info['file_type'] == 'pdf':
            filename = f"{paper_id}.pdf"
        elif paper_info['file_type'] == 'gzip':
            # Gzipped files are LaTeX source files
            filename = f"{paper_id}.gz"
        else:
            filename = f"{paper_id}.{paper_info['file_type']}"
        
        # Create output directory if it doesn't exist
        os.makedirs(output_dir, exist_ok=True)
        
        # Write to file
        output_path = os.path.join(output_dir, filename)
        with open(output_path, 'wb') as f:
            f.write(paper_data)
        
        logger.info(f"Extracted {len(paper_data)} bytes to: {output_path}")
        return output_path


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Extract arXiv papers using SQLite index"
    )
    parser.add_argument(
        "paper_id",
        help="arXiv paper ID to extract (e.g., 'hep-th/9605001' or '1234.5678')"
    )
    parser.add_argument(
        "--db-path",
        default="arxiv_index.db",
        help="Path to SQLite index database (default: arxiv_index.db)"
    )
    parser.add_argument(
        "--root-dir",
        required=True,
        help="Root directory containing arXiv bulk files (e.g., /raid1/arXiv/arXiv)"
    )
    parser.add_argument(
        "--output-dir",
        default=".",
        help="Output directory for extracted files (default: current directory)"
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
        # Create extractor
        extractor = PaperExtractor(args.db_path, args.root_dir)
        
        # Extract paper
        output_path = extractor.extract_to_file(args.paper_id, args.output_dir)
        
        print(f"Successfully extracted paper {args.paper_id} to: {output_path}")
        return 0
        
    except Exception as e:
        logger.error(f"Failed to extract paper: {e}")
        return 1


if __name__ == "__main__":
    exit(main())