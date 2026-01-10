import logging
import re
import sqlite3
import os
from typing import Optional, Tuple

import httpx

from .config import Settings

logger = logging.getLogger(__name__)


def normalize_paper_id(paper_id: str) -> str:
    """
    Normalize various arXiv paper ID formats to the canonical form stored in the database.

    Accepted formats:
    - arXiv:1501.00963v3 -> 1501.00963
    - arxiv:1501.00963 -> 1501.00963
    - 1501.00963v2 -> 1501.00963
    - https://arxiv.org/abs/1501.00963 -> 1501.00963
    - https://arxiv.org/pdf/1501.00963.pdf -> 1501.00963
    - astro-ph/0412561 -> astro-ph0412561
    - arXiv:astro-ph/0412561v1 -> astro-ph0412561
    """
    original = paper_id
    paper_id = paper_id.strip()

    # Handle URLs
    url_patterns = [
        r'https?://(?:export\.)?arxiv\.org/(?:abs|pdf)/(.+?)(?:\.pdf)?$',
        r'https?://(?:export\.)?arxiv\.org/(?:abs|pdf)/(.+?)(?:\.pdf)?(?:\?.*)?$',
    ]
    for pattern in url_patterns:
        match = re.match(pattern, paper_id, re.IGNORECASE)
        if match:
            paper_id = match.group(1)
            break

    # Strip "arXiv:" or "arxiv:" prefix
    paper_id = re.sub(r'^arxiv:\s*', '', paper_id, flags=re.IGNORECASE)

    # Strip version suffix (v1, v2, etc.)
    paper_id = re.sub(r'v\d+$', '', paper_id)

    # Handle old format with slash: astro-ph/0412561 -> astro-ph0412561
    # But preserve the category prefix
    if '/' in paper_id:
        parts = paper_id.split('/')
        if len(parts) == 2:
            category, number = parts
            # Old categories like astro-ph, hep-lat, etc.
            paper_id = f"{category}{number}"

    logger.debug(f"Normalized paper ID: '{original}' -> '{paper_id}'")
    return paper_id


class RetrievalError(Exception):
    """Custom exception for paper retrieval errors"""
    pass


class PaperRetriever:
    def __init__(self, settings: Settings):
        self.index_db_path = settings.INDEX_DB_PATH
        self.tar_dir_path = settings.TAR_DIR_PATH
        self.upstream_url = settings.UPSTREAM_SERVER_URL
        self.upstream_timeout = settings.UPSTREAM_TIMEOUT
        self.upstream_enabled = settings.UPSTREAM_ENABLED

        # Validate configuration at startup
        self._validate_config()

        # Connect to database
        try:
            self.db_connection = sqlite3.connect(self.index_db_path)
        except sqlite3.Error as e:
            raise RetrievalError(f"Failed to connect to database: {e}")
    
    def _validate_config(self):
        """Validate the configuration settings"""
        if not self.index_db_path:
            raise RetrievalError("INDEX_DB_PATH not configured")

        if not self.tar_dir_path:
            raise RetrievalError("TAR_DIR_PATH not configured")

        if not os.path.exists(self.index_db_path):
            raise RetrievalError(f"Database file not found: {self.index_db_path}")

        if not os.path.exists(self.tar_dir_path):
            raise RetrievalError(f"Root directory not found: {self.tar_dir_path}")

        # Check if the directory structure looks like arXiv (has year subdirectories)
        year_dirs = [d for d in os.listdir(self.tar_dir_path)
                    if os.path.isdir(os.path.join(self.tar_dir_path, d)) and d.isdigit()]

        if not year_dirs:
            # Warn instead of error - allows empty tar dir when upstream is configured
            if self.upstream_url and self.upstream_enabled:
                logger.warning(f"No year subdirectories in {self.tar_dir_path} - will rely on upstream for all papers")
            else:
                raise RetrievalError(f"Root directory doesn't contain expected year subdirectories: {self.tar_dir_path}")
    
    def _get_from_local(self, paper_id: str) -> Optional[bytes]:
        """
        Attempt to retrieve paper from local storage.
        Returns None if paper not found or tar file not available locally.
        """
        cursor = self.db_connection.cursor()
        cursor.execute(
            "SELECT archive_file, offset, size FROM paper_index WHERE paper_id = ?",
            (paper_id,)
        )
        result = cursor.fetchone()

        if result is None:
            return None

        archive_file, offset, size = result
        tar_file_path = os.path.join(self.tar_dir_path, archive_file)

        # Check if tar file exists locally
        if not os.path.exists(tar_file_path):
            logger.debug(f"Tar file not available locally: {tar_file_path}")
            return None

        try:
            with open(tar_file_path, 'rb') as file:
                file.seek(offset)
                return file.read(size)
        except (PermissionError, OSError) as e:
            logger.warning(f"Error reading local tar file {tar_file_path}: {e}")
            return None

    def _get_from_upstream(self, paper_id: str) -> Optional[bytes]:
        """
        Attempt to retrieve paper from upstream server.
        Returns None if upstream not configured, disabled, or request fails.
        """
        if not self.upstream_url or not self.upstream_enabled:
            return None

        try:
            with httpx.Client(timeout=self.upstream_timeout) as client:
                response = client.get(f"{self.upstream_url}/paper/{paper_id}")

                if response.status_code == 200:
                    return response.content
                elif response.status_code == 404:
                    return None
                else:
                    logger.warning(f"Upstream returned status {response.status_code} for {paper_id}")
                    return None

        except httpx.TimeoutException:
            logger.warning(f"Upstream timeout for paper {paper_id}")
            return None
        except httpx.RequestError as e:
            logger.warning(f"Upstream request error for paper {paper_id}: {e}")
            return None

    def get_source_by_id(self, paper_id: str) -> Optional[bytes]:
        """
        Get paper source by ID.
        Tries local storage first, then falls back to upstream if configured.
        Returns None if paper not found in both locations.
        """
        # Normalize the paper ID to handle various input formats
        paper_id = normalize_paper_id(paper_id)

        # Try local first
        result = self._get_from_local(paper_id)
        if result is not None:
            return result

        # Try upstream if configured
        result = self._get_from_upstream(paper_id)
        if result is not None:
            return result

        return None
    
    def get_detailed_error(self, paper_id: str) -> Tuple[str, str]:
        """
        Get detailed error information for debugging.
        Returns (error_type, error_message) tuple.
        """
        # Normalize the paper ID to match what was searched
        paper_id = normalize_paper_id(paper_id)

        try:
            # Check database connection
            cursor = self.db_connection.cursor()
            cursor.execute("SELECT COUNT(*) FROM paper_index")
            total_papers = cursor.fetchone()[0]
            
            if total_papers == 0:
                return ("empty_database", "The database contains no papers. Please run the indexing script first.")
            
            # Check if paper exists
            cursor.execute(
                "SELECT archive_file, offset, size FROM paper_index WHERE paper_id = ?",
                (paper_id,)
            )
            result = cursor.fetchone()
            
            if result is None:
                # Check for similar paper IDs
                cursor.execute(
                    "SELECT paper_id FROM paper_index WHERE paper_id LIKE ? LIMIT 5",
                    (f"%{paper_id[:6]}%",)
                )
                similar = cursor.fetchall()
                similar_ids = [row[0] for row in similar]
                
                if similar_ids:
                    return ("paper_not_found", f"Paper ID '{paper_id}' not found. Similar papers: {', '.join(similar_ids[:3])}")
                else:
                    return ("paper_not_found", f"Paper ID '{paper_id}' not found in the database.")
            
            # Paper exists in DB, check file access
            archive_file, offset, size = result
            tar_file_path = os.path.join(self.tar_dir_path, archive_file)

            if not os.path.exists(tar_file_path):
                msg = f"Archive file not found locally: {tar_file_path}"
                if self.upstream_url and self.upstream_enabled:
                    msg += f" (upstream at {self.upstream_url} was also unavailable or returned not found)"
                return ("archive_missing", msg)

            if not os.access(tar_file_path, os.R_OK):
                return ("permission_denied", f"Permission denied accessing archive file: {tar_file_path}")

            return ("unknown_error", "Unknown error occurred during paper retrieval.")
            
        except sqlite3.Error as e:
            return ("database_error", f"Database error: {e}")
        except Exception as e:
            return ("system_error", f"System error: {e}")