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


def get_expected_tar_pattern(paper_id: str) -> Optional[Dict[str, str]]:
    """
    Determine the expected tar file pattern for a paper ID.

    arXiv distributes papers in bulk tar files organized by year/month.
    This function extracts the pattern so users know which files to download.

    Returns dict with:
    - year_dir: The year directory (e.g., "2021")
    - yymm: The year-month code (e.g., "2103")
    - pdf_pattern: Pattern for PDF tar files (e.g., "arXiv_pdf_2103_*.tar")
    - src_pattern: Pattern for source tar files (e.g., "arXiv_src_2103_*.tar")
    - category: Category for old-format papers (e.g., "astro-ph"), None for modern

    Returns None if paper ID format is not recognized.
    """
    base_id, _ = parse_paper_id(paper_id)

    # Modern format: YYMM.NNNNN (e.g., 2103.06497)
    modern_match = re.match(r'^(\d{2})(\d{2})\.(\d+)$', base_id)
    if modern_match:
        yy, mm, _ = modern_match.groups()
        year = 2000 + int(yy) if int(yy) < 90 else 1900 + int(yy)
        yymm = f"{yy}{mm}"
        return {
            "year_dir": str(year),
            "yymm": yymm,
            "pdf_pattern": f"arXiv_pdf_{yymm}_*.tar",
            "src_pattern": f"arXiv_src_{yymm}_*.tar",
            "category": None,
        }

    # Old format: categoryYYMMNNN (e.g., astro-ph0412561, hep-lat9107001)
    old_match = re.match(r'^([a-z-]+)(\d{2})(\d{2})(\d+)$', base_id, re.IGNORECASE)
    if old_match:
        category, yy, mm, _ = old_match.groups()
        year = 2000 + int(yy) if int(yy) < 90 else 1900 + int(yy)
        yymm = f"{yy}{mm}"
        return {
            "year_dir": str(year),
            "yymm": yymm,
            "pdf_pattern": f"arXiv_pdf_{category}_{yymm}_*.tar",
            "src_pattern": f"arXiv_src_{category}_{yymm}_*.tar",
            "category": category,
        }

    return None


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

        # arXiv direct fallback settings
        self.arxiv_fallback_enabled = settings.ARXIV_FALLBACK_ENABLED
        self.arxiv_timeout = settings.ARXIV_TIMEOUT

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

    def _get_info_from_upstream(self, paper_id: str) -> Optional[Dict[str, Any]]:
        """
        Attempt to get paper metadata from upstream server's /info endpoint.
        Returns None if upstream not configured, disabled, or request fails.
        """
        if not self.upstream_url or not self.upstream_enabled:
            return None

        try:
            with httpx.Client(timeout=self.upstream_timeout) as client:
                response = client.get(f"{self.upstream_url}/paper/{paper_id}/info")

                if response.status_code == 200:
                    return response.json()
                elif response.status_code == 404:
                    return None
                else:
                    logger.warning(f"Upstream info returned status {response.status_code} for {paper_id}")
                    return None

        except httpx.TimeoutException:
            logger.warning(f"Upstream info timeout for paper {paper_id}")
            return None
        except httpx.RequestError as e:
            logger.warning(f"Upstream info request error for paper {paper_id}: {e}")
            return None

    def _get_from_arxiv(
        self,
        paper_id: str,
        format: Optional[str] = None
    ) -> Optional[Tuple[bytes, str]]:
        """
        Attempt to retrieve paper directly from arXiv.org.

        Args:
            paper_id: The paper ID (can include version, e.g., "1501.00963v3")
            format: Optional format preference ("pdf" or "source")

        Returns:
            Tuple of (content_bytes, source_type) where source_type is "arxiv_pdf" or "arxiv_source",
            or None if not available or fallback is disabled.
        """
        if not self.arxiv_fallback_enabled:
            return None

        base_id, version = parse_paper_id(paper_id)
        arxiv_id = f"{base_id}v{version}" if version else base_id

        # For old-format IDs, need to restore the slash for arXiv URLs
        # e.g., "astro-ph0412561" -> "astro-ph/0412561"
        old_format_match = re.match(r'^([a-z-]+)(\d+)$', base_id, re.IGNORECASE)
        if old_format_match:
            category, number = old_format_match.groups()
            arxiv_id = f"{category}/{number}"
            if version:
                arxiv_id = f"{arxiv_id}v{version}"

        try:
            with httpx.Client(timeout=self.arxiv_timeout, follow_redirects=True) as client:
                # Try PDF first if preferred or no preference
                if format in (None, "preferred", "pdf"):
                    pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
                    logger.debug(f"Trying arXiv PDF: {pdf_url}")
                    response = client.get(pdf_url)
                    if response.status_code == 200 and response.content[:4] == b'%PDF':
                        logger.info(f"Retrieved {paper_id} from arXiv (PDF)")
                        return (response.content, "arxiv_pdf")

                # Try source if preferred or PDF failed/not preferred
                if format in (None, "preferred", "source"):
                    source_url = f"https://export.arxiv.org/e-print/{arxiv_id}"
                    logger.debug(f"Trying arXiv source: {source_url}")
                    response = client.get(source_url)
                    if response.status_code == 200 and len(response.content) > 0:
                        logger.info(f"Retrieved {paper_id} from arXiv (source)")
                        return (response.content, "arxiv_source")

                logger.debug(f"Paper {paper_id} not found on arXiv")
                return None

        except httpx.TimeoutException:
            logger.warning(f"arXiv timeout for paper {paper_id}")
            return None
        except httpx.RequestError as e:
            logger.warning(f"arXiv request error for paper {paper_id}: {e}")
            return None

    def _check_arxiv_availability(self, paper_id: str) -> Optional[Dict[str, Any]]:
        """
        Check if a paper is available on arXiv without downloading it.
        Uses HEAD requests to check availability.

        Returns dict with paper info if available, None otherwise.
        """
        if not self.arxiv_fallback_enabled:
            return None

        base_id, version = parse_paper_id(paper_id)
        arxiv_id = f"{base_id}v{version}" if version else base_id

        # For old-format IDs, restore the slash
        old_format_match = re.match(r'^([a-z-]+)(\d+)$', base_id, re.IGNORECASE)
        if old_format_match:
            category, number = old_format_match.groups()
            arxiv_id = f"{category}/{number}"
            if version:
                arxiv_id = f"{arxiv_id}v{version}"

        try:
            with httpx.Client(timeout=self.arxiv_timeout, follow_redirects=True) as client:
                # Check PDF availability
                pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
                response = client.head(pdf_url)
                if response.status_code == 200:
                    # Extract year from paper ID
                    year = None
                    year_match = re.match(r'^(\d{2})\d{2}\.', base_id)
                    if year_match:
                        yy = int(year_match.group(1))
                        year = 2000 + yy if yy < 90 else 1900 + yy

                    return {
                        "paper_id": base_id,
                        "requested_version": version,
                        "file_type": "pdf",
                        "format": "pdf",
                        "size_bytes": None,  # HEAD doesn't always return Content-Length
                        "year": year,
                        "locally_available": False,
                        "source": "arxiv",
                    }

                return None

        except (httpx.TimeoutException, httpx.RequestError) as e:
            logger.debug(f"arXiv availability check failed for {paper_id}: {e}")
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

        Checks local database first, then upstream server if configured.

        Returns dict with:
        - paper_id: The normalized paper ID
        - requested_version: Version requested (if any)
        - file_type: Raw file type from database (pdf, gzip, tar, unknown)
        - format: Simplified format category (pdf, source, unknown)
        - size_bytes: File size in bytes
        - year: Publication year
        - locally_available: Whether the paper is stored locally
        - upstream_configured: Whether upstream fallback is available
        - source: Where the metadata came from ("local" or "upstream")

        Returns None if paper not found in either location.
        """
        lookup_id, requested_version, version_required = self._resolve_paper_id(paper_id)

        # Try to find the paper locally
        metadata = self._lookup_paper_metadata(lookup_id)

        # If versioned lookup failed, try base ID (only if version not required)
        if metadata is None and not version_required:
            base_id, _ = parse_paper_id(paper_id)
            metadata = self._lookup_paper_metadata(base_id)

        if metadata is not None:
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
                "source": "local",
            }

        # Not found locally - try upstream
        upstream_info = self._get_info_from_upstream(paper_id)
        if upstream_info is not None:
            # Add source indicator and ensure consistent structure
            upstream_info["source"] = "upstream"
            upstream_info["locally_available"] = False
            return upstream_info

        # Not found in upstream - check arXiv availability
        arxiv_info = self._check_arxiv_availability(paper_id)
        if arxiv_info is not None:
            arxiv_info["upstream_configured"] = bool(self.upstream_url and self.upstream_enabled)
            arxiv_info["arxiv_fallback_enabled"] = self.arxiv_fallback_enabled
            return arxiv_info

        return None

    def get_source_by_id(
        self,
        paper_id: str,
        format: Optional[str] = None
    ) -> Dict[str, Any]:
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
            Dict with:
            - On success:
                - content: bytes
                - content_type: str (e.g., "application/pdf")
                - error: None
                - paper_id: str (normalized ID)
                - file_type: str (pdf, gzip, tar, unknown)
                - format: str (pdf, source, unknown)
                - year: int or None
                - version: int or None (requested version)
                - source: str ("local", "cache", or "upstream")
            - On error:
                - content: None
                - content_type: None
                - error: str ("not_found", "format_unavailable", "version_not_found")
        """
        lookup_id, requested_version, version_required = self._resolve_paper_id(paper_id)
        base_id, _ = parse_paper_id(paper_id)

        # Check format filter against metadata first (if we have local metadata)
        metadata = self._lookup_paper_metadata(lookup_id)

        # Track if we need to try arXiv for a specific version
        try_arxiv_for_version = False
        if metadata is None and version_required:
            # Version not in local DB - will try arXiv later
            try_arxiv_for_version = True

        # If versioned lookup failed, try base ID for local/upstream
        if metadata is None:
            lookup_id = base_id
            metadata = self._lookup_paper_metadata(base_id)

        # Check format compatibility with local metadata.
        # If local format doesn't match, skip local retrieval but still try
        # upstream/arXiv (they may have the requested format).
        local_format_mismatch = False
        if metadata is not None and format and format != "preferred":
            paper_format = metadata["format"]
            if format == "pdf" and paper_format != "pdf":
                local_format_mismatch = True
            elif format == "source" and paper_format not in ("source",):
                local_format_mismatch = True

        # Helper to build success response
        def success_response(content: bytes, source: str, meta: Optional[Dict] = None) -> Dict[str, Any]:
            content_type = detect_content_type(content)
            file_type = "pdf" if content_type == "application/pdf" else \
                        "gzip" if content_type == "application/gzip" else \
                        "tar" if content_type == "application/x-tar" else "unknown"
            fmt = "pdf" if file_type == "pdf" else "source" if file_type in ("gzip", "tar") else "unknown"

            return {
                "content": content,
                "content_type": content_type,
                "error": None,
                "paper_id": meta["paper_id"] if meta else lookup_id,
                "file_type": meta["file_type"] if meta else file_type,
                "format": meta["format"] if meta else fmt,
                "year": meta["year"] if meta else None,
                "version": requested_version,
                "source": source,
            }

        # Try cache and local storage (skip if local format doesn't match request)
        if not local_format_mismatch:
            if self.cache:
                result = self.cache.get(lookup_id)
                if result is not None:
                    return success_response(result, "cache", metadata)

            result = self._get_from_local(lookup_id)
            if result is not None:
                if self.cache:
                    self.cache.put(lookup_id, result)
                return success_response(result, "local", metadata)

        # Try upstream if configured
        result = self._get_from_upstream(lookup_id)
        if result is not None:
            # Verify format from actual content if we didn't have metadata
            if format and format != "preferred":
                content_type = detect_content_type(result)
                actual_format = "pdf" if content_type == "application/pdf" else "source"
                if format != actual_format:
                    return {"content": None, "content_type": None, "error": "format_unavailable"}

            if self.cache:
                self.cache.put(lookup_id, result)

            # Try to get metadata from upstream for year info
            upstream_meta = self._get_info_from_upstream(paper_id)
            return success_response(result, "upstream", upstream_meta)

        # Try arXiv direct fallback as last resort
        # Use original paper_id to preserve version info
        arxiv_result = self._get_from_arxiv(paper_id, format)
        if arxiv_result is not None:
            content, source_type = arxiv_result

            # Verify format from actual content
            if format and format != "preferred":
                content_type = detect_content_type(content)
                actual_format = "pdf" if content_type == "application/pdf" else "source"
                if format != actual_format:
                    return {"content": None, "content_type": None, "error": "format_unavailable"}

            # Cache the result from arXiv
            cache_key = f"{base_id}v{requested_version}" if requested_version else base_id
            if self.cache:
                self.cache.put(cache_key, content)

            return success_response(content, source_type, None)

        # All sources exhausted
        if try_arxiv_for_version:
            return {"content": None, "content_type": None, "error": "version_not_found"}
        if local_format_mismatch:
            return {"content": None, "content_type": None, "error": "format_unavailable"}
        return {"content": None, "content_type": None, "error": "not_found"}

    def get_random_paper(
        self,
        format: Optional[str] = None,
        category: Optional[str] = None,
        local_only: bool = True
    ) -> Optional[Dict[str, Any]]:
        """
        Get a random paper from the database.

        Args:
            format: Optional format filter ("pdf" or "source")
            category: Optional category filter (e.g., "astro-ph", "hep-lat", "cs.AI")
                     Searches both paper_id (old-format) and categories column (all papers)
            local_only: If True, only return papers whose tar files exist locally

        Returns:
            Dict with paper metadata, or None if no matching papers found.
        """
        cursor = self.db_connection.cursor()

        # If local_only, first get list of tar files that exist locally
        available_archives = None
        if local_only:
            available_archives = set()
            # Walk through tar directory to find available archives
            for root, dirs, files in os.walk(self.tar_dir_path):
                for f in files:
                    if f.endswith('.tar'):
                        # Get relative path from tar_dir_path
                        rel_path = os.path.relpath(os.path.join(root, f), self.tar_dir_path)
                        available_archives.add(rel_path)

            if not available_archives:
                return None

        # Build the query with optional filters
        conditions = []
        params = []

        # Format filter
        if format == "pdf":
            conditions.append("file_type = 'pdf'")
        elif format == "source":
            conditions.append("file_type IN ('gzip', 'tar')")

        # Category filter - searches both paper_id (old-format) and categories column
        # Examples: "astro-ph" matches "astro-ph0412561" or categories containing "astro-ph.GA"
        if category:
            category_lower = category.lower()
            # Check if categories column exists
            if self._has_categories_column():
                # Match paper_id starting with category (old format) OR categories containing category
                conditions.append("(paper_id LIKE ? OR categories LIKE ?)")
                params.append(f"{category_lower}%")
                params.append(f"%{category_lower}%")
            else:
                # Fall back to paper_id only (old format papers)
                conditions.append("paper_id LIKE ?")
                params.append(f"{category_lower}%")

        # Filter by available archives if local_only
        if available_archives:
            placeholders = ",".join(["?" for _ in available_archives])
            conditions.append(f"archive_file IN ({placeholders})")
            params.extend(available_archives)

        # Build WHERE clause
        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        # Get a random paper
        query = f"""
            SELECT paper_id, archive_file, offset, size, file_type, year
            FROM paper_index
            {where_clause}
            ORDER BY RANDOM()
            LIMIT 1
        """

        cursor.execute(query, params)
        row = cursor.fetchone()

        if not row:
            return None

        paper_id, archive_file, offset, size, file_type, year = row
        tar_file_path = os.path.join(self.tar_dir_path, archive_file)

        return {
            "paper_id": paper_id,
            "archive_file": archive_file,
            "file_type": file_type,
            "format": get_format_from_file_type(file_type),
            "size_bytes": size,
            "year": year,
            "locally_available": os.path.exists(tar_file_path),
        }

    def _has_categories_column(self) -> bool:
        """Check if the categories column exists in paper_index table."""
        cursor = self.db_connection.cursor()
        cursor.execute("PRAGMA table_info(paper_index)")
        columns = [row[1] for row in cursor.fetchall()]
        return 'categories' in columns

    def get_available_categories(self) -> Dict[str, Any]:
        """
        Get list of available categories from the database.

        Returns dict with:
        - legacy_categories: Categories from old-format paper IDs (e.g., "astro-ph", "hep-lat")
        - modern_categories: Categories from the categories column (e.g., "astro-ph.GA", "cs.AI")
        - all_categories: Combined unique list of category prefixes
        """
        cursor = self.db_connection.cursor()
        legacy_categories = set()
        modern_categories = set()

        # 1. Extract categories from old-format paper IDs
        cursor.execute("""
            SELECT DISTINCT paper_id FROM paper_index
            WHERE paper_id GLOB '[a-z]*'
            AND paper_id NOT GLOB '[0-9]*'
        """)

        category_pattern = re.compile(r'^([a-z]+-?[a-z]*)\d', re.IGNORECASE)
        for row in cursor.fetchall():
            paper_id = row[0]
            match = category_pattern.match(paper_id)
            if match:
                category = match.group(1).lower()
                if len(category) >= 2 and not category.isdigit():
                    legacy_categories.add(category)

        # 2. Extract categories from the categories column (modern format)
        # Only if the column exists (requires running fetch_categories.py)
        if self._has_categories_column():
            cursor.execute("""
                SELECT DISTINCT categories FROM paper_index
                WHERE categories IS NOT NULL AND categories != ''
            """)

            for row in cursor.fetchall():
                cats = row[0].split()
                for cat in cats:
                    modern_categories.add(cat.lower())

        # 3. Build combined list with category prefixes (e.g., "astro-ph" from "astro-ph.GA")
        all_prefixes = set()
        for cat in legacy_categories:
            all_prefixes.add(cat)
        for cat in modern_categories:
            # Add both full category and prefix
            all_prefixes.add(cat)
            if '.' in cat:
                prefix = cat.split('.')[0]
                all_prefixes.add(prefix)

        return {
            "legacy_categories": sorted(legacy_categories),
            "modern_categories": sorted(modern_categories),
            "all_categories": sorted(all_prefixes),
            "categories_column_exists": self._has_categories_column(),
        }

    def get_detailed_error(self, paper_id: str) -> Dict[str, Any]:
        """
        Get detailed error information for debugging.

        Returns dict with:
        - error_type: str - Type of error (paper_not_found, archive_missing, etc.)
        - error_message: str - Human-readable error message
        - tar_hint: Optional[dict] - Expected tar file pattern info (if paper not found)
        - similar_ids: Optional[list] - Similar paper IDs found in database
        """
        # Normalize the paper ID to match what was searched
        original_id = paper_id
        paper_id = normalize_paper_id(paper_id)

        # Get tar file hint for this paper ID
        tar_hint = get_expected_tar_pattern(original_id)

        try:
            # Check database connection
            cursor = self.db_connection.cursor()
            cursor.execute("SELECT COUNT(*) FROM paper_index")
            total_papers = cursor.fetchone()[0]

            if total_papers == 0:
                return {
                    "error_type": "empty_database",
                    "error_message": "The database contains no papers. Please run the indexing script first.",
                    "tar_hint": tar_hint,
                    "similar_ids": None,
                }

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

                msg = f"Paper ID '{paper_id}' not found in the database."
                if similar_ids:
                    msg = f"Paper ID '{paper_id}' not found. Similar papers: {', '.join(similar_ids[:3])}"

                return {
                    "error_type": "paper_not_found",
                    "error_message": msg,
                    "tar_hint": tar_hint,
                    "similar_ids": similar_ids if similar_ids else None,
                }

            # Paper exists in DB, check file access
            archive_file, offset, size = result
            tar_file_path = os.path.join(self.tar_dir_path, archive_file)

            if not os.path.exists(tar_file_path):
                msg = f"Archive file not found locally: {tar_file_path}"
                if self.upstream_url and self.upstream_enabled:
                    msg += f" (upstream at {self.upstream_url} was also unavailable or returned not found)"
                return {
                    "error_type": "archive_missing",
                    "error_message": msg,
                    "tar_hint": None,  # We know exactly which file is needed
                    "archive_file": archive_file,
                    "similar_ids": None,
                }

            if not os.access(tar_file_path, os.R_OK):
                return {
                    "error_type": "permission_denied",
                    "error_message": f"Permission denied accessing archive file: {tar_file_path}",
                    "tar_hint": None,
                    "similar_ids": None,
                }

            return {
                "error_type": "unknown_error",
                "error_message": "Unknown error occurred during paper retrieval.",
                "tar_hint": None,
                "similar_ids": None,
            }

        except sqlite3.Error as e:
            return {
                "error_type": "database_error",
                "error_message": f"Database error: {e}",
                "tar_hint": tar_hint,
                "similar_ids": None,
            }
        except Exception as e:
            return {
                "error_type": "system_error",
                "error_message": f"System error: {e}",
                "tar_hint": tar_hint,
                "similar_ids": None,
            }