import logging
import re
import sqlite3
import os
from typing import Optional, Tuple, Dict, Any

import httpx

from .config import Settings
from .cache import PaperCache

logger = logging.getLogger(__name__)


def parse_paper_id(paper_id: str) -> Tuple[str, Optional[int]]:
    """
    Parse a paper ID into (base_id, version) tuple.

    Handles various arXiv formats and extracts version if present.
    Returns (normalized_base_id, version_number) where version_number is None if not specified.

    Examples:
    - "arXiv:1501.00963v3" -> ("1501.00963", 3)
    - "1501.00963" -> ("1501.00963", None)
    - "astro-ph/0412561v1" -> ("astro-ph0412561", 1)
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

    # Extract version suffix (v1, v2, etc.) before removing it
    version = None
    version_match = re.search(r'v(\d+)$', paper_id)
    if version_match:
        version = int(version_match.group(1))
        paper_id = paper_id[:version_match.start()]

    # Handle old format with slash: astro-ph/0412561 -> astro-ph0412561
    if '/' in paper_id:
        parts = paper_id.split('/')
        if len(parts) == 2:
            category, number = parts
            paper_id = f"{category}{number}"

    logger.debug(f"Parsed paper ID: '{original}' -> ('{paper_id}', v{version})")
    return paper_id, version


def normalize_paper_id(paper_id: str) -> str:
    """
    Normalize paper ID to base form (without version).
    For backward compatibility.
    """
    base_id, _ = parse_paper_id(paper_id)
    return base_id


def detect_content_type(content: bytes) -> str:
    """
    Detect the content type from the first bytes of the content.

    Returns:
    - "application/pdf" for PDF files
    - "application/gzip" for gzip-compressed files
    - "application/x-tar" for tar archives
    - "application/octet-stream" for unknown
    """
    if content[:4] == b'%PDF':
        return "application/pdf"
    elif content[:2] == b'\x1f\x8b':
        return "application/gzip"
    elif len(content) >= 262 and content[257:262] == b'ustar':
        return "application/x-tar"
    else:
        return "application/octet-stream"


def get_format_from_file_type(file_type: str) -> str:
    """Map database file_type to format category."""
    if file_type == "pdf":
        return "pdf"
    elif file_type in ("gzip", "tar"):
        return "source"
    else:
        return "unknown"


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

        # Initialize cache if configured
        self.cache: Optional[PaperCache] = None
        if settings.CACHE_DIR_PATH:
            self.cache = PaperCache(
                cache_dir=settings.CACHE_DIR_PATH,
                max_size_gb=settings.CACHE_MAX_SIZE_GB
            )

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
    
    def _lookup_paper_metadata(self, paper_id: str) -> Optional[Dict[str, Any]]:
        """
        Look up paper metadata from the database.
        Returns dict with archive_file, offset, size, file_type, year or None if not found.
        """
        cursor = self.db_connection.cursor()
        cursor.execute(
            "SELECT archive_file, offset, size, file_type, year FROM paper_index WHERE paper_id = ?",
            (paper_id,)
        )
        result = cursor.fetchone()

        if result is None:
            return None

        return {
            "paper_id": paper_id,
            "archive_file": result[0],
            "offset": result[1],
            "size": result[2],
            "file_type": result[3],
            "year": result[4],
            "format": get_format_from_file_type(result[3]),
        }

    def _get_from_local(self, paper_id: str) -> Optional[bytes]:
        """
        Attempt to retrieve paper from local storage.
        Returns None if paper not found or tar file not available locally.
        """
        metadata = self._lookup_paper_metadata(paper_id)
        if metadata is None:
            return None

        tar_file_path = os.path.join(self.tar_dir_path, metadata["archive_file"])

        # Check if tar file exists locally
        if not os.path.exists(tar_file_path):
            logger.debug(f"Tar file not available locally: {tar_file_path}")
            return None

        try:
            with open(tar_file_path, 'rb') as file:
                file.seek(metadata["offset"])
                return file.read(metadata["size"])
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

    def _resolve_paper_id(self, paper_id: str) -> Tuple[str, Optional[int], bool]:
        """
        Resolve the paper ID to lookup in the database.

        Returns (lookup_id, requested_version, version_required) tuple.
        - lookup_id: The ID to use for database lookup
        - requested_version: Version number if specified by caller
        - version_required: True if caller specified a version (must match exactly)
        """
        base_id, version = parse_paper_id(paper_id)

        if version is not None:
            # Caller requested specific version - try versioned ID first
            versioned_id = f"{base_id}v{version}"
            return versioned_id, version, True
        else:
            # No version specified - use base ID
            return base_id, None, False

    def get_paper_info(self, paper_id: str) -> Optional[Dict[str, Any]]:
        """
        Get metadata about a paper without retrieving its content.

        Returns dict with:
        - paper_id: The normalized paper ID
        - requested_version: Version requested (if any)
        - file_type: Raw file type from database (pdf, gzip, tar, unknown)
        - format: Simplified format category (pdf, source, unknown)
        - size_bytes: File size in bytes
        - year: Publication year
        - available: Whether the paper is available for retrieval

        Returns None if paper not found.
        """
        lookup_id, requested_version, version_required = self._resolve_paper_id(paper_id)

        # Try to find the paper
        metadata = self._lookup_paper_metadata(lookup_id)

        # If versioned lookup failed and version was required, don't fall back
        if metadata is None and version_required:
            return None

        # If versioned lookup failed, try base ID
        if metadata is None:
            base_id, _ = parse_paper_id(paper_id)
            metadata = self._lookup_paper_metadata(base_id)

        if metadata is None:
            return None

        # Check if tar file is available locally
        tar_file_path = os.path.join(self.tar_dir_path, metadata["archive_file"])
        locally_available = os.path.exists(tar_file_path)

        return {
            "paper_id": metadata["paper_id"],
            "requested_version": requested_version,
            "file_type": metadata["file_type"],
            "format": metadata["format"],
            "size_bytes": metadata["size"],
            "year": metadata["year"],
            "locally_available": locally_available,
            "upstream_configured": bool(self.upstream_url and self.upstream_enabled),
        }

    def get_source_by_id(
        self,
        paper_id: str,
        format: Optional[str] = None
    ) -> Tuple[Optional[bytes], Optional[str], Optional[str]]:
        """
        Get paper source by ID with optional format filtering.

        Args:
            paper_id: The arXiv paper ID (supports various formats including versioned)
            format: Optional format filter:
                - "pdf": Only return if paper is PDF format
                - "source": Only return if paper is source (gzip/tar)
                - "preferred": Return whatever is available (default behavior)
                - None: Same as "preferred"

        Returns:
            Tuple of (content, content_type, error_reason):
            - On success: (bytes, content_type_string, None)
            - On not found: (None, None, "not_found")
            - On format mismatch: (None, None, "format_unavailable")
            - On version mismatch: (None, None, "version_not_found")
        """
        lookup_id, requested_version, version_required = self._resolve_paper_id(paper_id)

        # Check format filter against metadata first (if we have local metadata)
        metadata = self._lookup_paper_metadata(lookup_id)

        # If versioned lookup failed and version was required, return error
        if metadata is None and version_required:
            return None, None, "version_not_found"

        # If versioned lookup failed, try base ID
        if metadata is None:
            base_id, _ = parse_paper_id(paper_id)
            lookup_id = base_id
            metadata = self._lookup_paper_metadata(base_id)

        # Check format compatibility before fetching content
        if metadata is not None and format and format != "preferred":
            paper_format = metadata["format"]
            if format == "pdf" and paper_format != "pdf":
                return None, None, "format_unavailable"
            elif format == "source" and paper_format not in ("source",):
                return None, None, "format_unavailable"

        # Try cache first (enables offline access when upstream is down)
        if self.cache:
            result = self.cache.get(lookup_id)
            if result is not None:
                content_type = detect_content_type(result)
                return result, content_type, None

        # Try local storage
        result = self._get_from_local(lookup_id)
        if result is not None:
            if self.cache:
                self.cache.put(lookup_id, result)
            content_type = detect_content_type(result)
            return result, content_type, None

        # Try upstream if configured
        result = self._get_from_upstream(lookup_id)
        if result is not None:
            # Verify format from actual content if we didn't have metadata
            if format and format != "preferred":
                content_type = detect_content_type(result)
                actual_format = "pdf" if content_type == "application/pdf" else "source"
                if format != actual_format:
                    return None, None, "format_unavailable"

            if self.cache:
                self.cache.put(lookup_id, result)
            content_type = detect_content_type(result)
            return result, content_type, None

        return None, None, "not_found"
    
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