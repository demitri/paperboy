"""
Disk-based LRU cache for IR (Intermediate Representation) packages.

IR packages are generated via LaTeXML and take 5-30 seconds per paper,
so caching provides significant performance benefits for repeated requests.
"""

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class IRCache:
    """
    LRU disk cache for IR packages.

    IR packages are stored as individual files with keys: {paper_id}_{profile}
    File modification times are used to track access order.
    When the cache exceeds the max size, least recently used packages are evicted.
    """

    def __init__(self, cache_dir: str, max_size_gb: float = 5.0):
        """
        Initialize the IR cache.

        Args:
            cache_dir: Directory to store cached IR packages
            max_size_gb: Maximum cache size in gigabytes (default 5GB)
        """
        self.cache_dir = Path(cache_dir)
        self.max_size_bytes = int(max_size_gb * 1024 * 1024 * 1024)

        # Create cache directory if it doesn't exist
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"IR cache initialized at {self.cache_dir} (max size: {max_size_gb}GB)")

    def _sanitize_paper_id(self, paper_id: str) -> str:
        """Convert paper ID to a safe filename component."""
        return paper_id.replace('/', '_').replace('\\', '_').replace(':', '_')

    def _get_cache_key(self, paper_id: str, profile: str) -> str:
        """Generate cache key from paper ID and profile."""
        return f"{self._sanitize_paper_id(paper_id)}_{profile}"

    def _get_cache_path(self, paper_id: str, profile: str) -> Path:
        """Get the file path for a cached IR package."""
        return self.cache_dir / self._get_cache_key(paper_id, profile)

    def get(self, paper_id: str, profile: str) -> Optional[bytes]:
        """
        Retrieve an IR package from the cache.

        Updates the file's modification time to mark it as recently used.

        Args:
            paper_id: The normalized paper ID
            profile: The IR profile (e.g., 'text-only', 'full')

        Returns:
            IR package contents as bytes, or None if not in cache
        """
        cache_path = self._get_cache_path(paper_id, profile)

        if not cache_path.exists():
            return None

        try:
            # Read the cached content
            content = cache_path.read_bytes()

            # Update modification time to mark as recently used
            os.utime(cache_path, None)

            logger.debug(f"IR cache hit for paper {paper_id} (profile={profile})")
            return content

        except (OSError, IOError) as e:
            logger.warning(f"Error reading cached IR package {paper_id}: {e}")
            return None

    def put(self, paper_id: str, profile: str, content: bytes) -> bool:
        """
        Store an IR package in the cache.

        Evicts least recently used packages if necessary to stay under the size limit.

        Args:
            paper_id: The normalized paper ID
            profile: The IR profile (e.g., 'text-only', 'full')
            content: IR package contents as bytes

        Returns:
            True if cached successfully, False otherwise
        """
        cache_path = self._get_cache_path(paper_id, profile)
        content_size = len(content)

        # Don't cache files larger than the max cache size
        if content_size > self.max_size_bytes:
            logger.warning(
                f"IR package {paper_id} ({content_size} bytes) exceeds cache size limit "
                f"({self.max_size_bytes} bytes), not caching"
            )
            return False

        try:
            # Evict old entries to make room
            self._evict_if_needed(content_size)

            # Write the content
            cache_path.write_bytes(content)

            logger.debug(f"Cached IR package {paper_id} (profile={profile}, {content_size} bytes)")
            return True

        except (OSError, IOError) as e:
            logger.warning(f"Error caching IR package {paper_id}: {e}")
            return False

    def _get_cache_entries(self) -> list:
        """
        Get all cache entries sorted by modification time (oldest first).

        Returns:
            List of (path, size, mtime) tuples, sorted by mtime ascending
        """
        entries = []

        try:
            for entry in self.cache_dir.iterdir():
                if entry.is_file():
                    stat = entry.stat()
                    entries.append((entry, stat.st_size, stat.st_mtime))
        except OSError as e:
            logger.warning(f"Error listing IR cache directory: {e}")
            return []

        # Sort by modification time (oldest first for LRU eviction)
        entries.sort(key=lambda x: x[2])
        return entries

    def _get_current_size(self) -> int:
        """Get the current total size of cached files in bytes."""
        return sum(entry[1] for entry in self._get_cache_entries())

    def _evict_if_needed(self, new_content_size: int) -> None:
        """
        Evict least recently used entries if needed to fit new content.

        Args:
            new_content_size: Size of the content being added
        """
        entries = self._get_cache_entries()
        current_size = sum(entry[1] for entry in entries)
        target_size = self.max_size_bytes - new_content_size

        if current_size <= target_size:
            return

        # Evict oldest entries until we have enough space
        for path, size, mtime in entries:
            if current_size <= target_size:
                break

            try:
                path.unlink()
                current_size -= size
                logger.debug(f"Evicted cached IR package {path.name} ({size} bytes)")
            except OSError as e:
                logger.warning(f"Error evicting cached IR package {path.name}: {e}")

    def get_stats(self) -> dict:
        """
        Get cache statistics.

        Returns:
            Dictionary with cache statistics
        """
        entries = self._get_cache_entries()
        current_size = sum(entry[1] for entry in entries)

        return {
            "cache_dir": str(self.cache_dir),
            "max_size_bytes": self.max_size_bytes,
            "max_size_gb": self.max_size_bytes / (1024 * 1024 * 1024),
            "current_size_bytes": current_size,
            "current_size_mb": current_size / (1024 * 1024),
            "utilization_percent": (current_size / self.max_size_bytes * 100) if self.max_size_bytes > 0 else 0,
            "num_packages": len(entries),
        }

    def clear(self) -> int:
        """
        Clear all cached IR packages.

        Returns:
            Number of packages removed
        """
        count = 0
        for entry in self.cache_dir.iterdir():
            if entry.is_file():
                try:
                    entry.unlink()
                    count += 1
                except OSError as e:
                    logger.warning(f"Error removing cached IR package {entry.name}: {e}")

        logger.info(f"Cleared {count} IR packages from cache")
        return count
