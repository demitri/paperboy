import logging
import re
import sqlite3
import os
import zipfile
from typing import Optional, Tuple, Dict, Any

import httpx

from .config import Settings

logger = logging.getLogger(__name__)


def parse_patent_id(patent_id: str) -> Tuple[str, Optional[str]]:
    """
    Parse a patent ID into (bare_number, kind_code).

    Strips 'US' prefix and extracts trailing kind code.
    Kind codes are one uppercase letter optionally followed by one digit (e.g., B2, A1, S, E).

    Examples:
        US11123456B2    -> ("11123456", "B2")
        US20200123456A1 -> ("20200123456", "A1")
        11123456        -> ("11123456", None)
        11123456B2      -> ("11123456", "B2")
        D0987654S       -> ("D0987654", "S")
        RE12345E        -> ("RE12345", "E")
    """
    pid = patent_id.strip()

    # Strip leading "US" prefix (case-insensitive)
    if pid.upper().startswith("US"):
        pid = pid[2:]

    # Extract trailing kind code: one uppercase letter optionally followed by one digit
    kind_match = re.search(r'([A-Z]\d?)$', pid)
    kind_code = None
    if kind_match:
        candidate = kind_match.group(1)
        bare = pid[:kind_match.start()]
        # Only treat as kind code if what remains looks like a number
        # (or starts with D/RE/PP for design/reissue/plant patents)
        if bare and (bare.isdigit() or re.match(r'^(D|RE|PP)\d', bare)):
            kind_code = candidate
            pid = bare

    logger.debug(f"Parsed patent ID: '{patent_id}' -> ('{pid}', {kind_code})")
    return pid, kind_code


def normalize_patent_id(patent_id: str) -> str:
    """
    Normalize patent ID to bare document number.

    US11123456B2    -> 11123456
    US20200123456A1 -> 20200123456
    11123456        -> 11123456
    """
    bare, _ = parse_patent_id(patent_id)
    return bare


class PatentRetriever:
    def __init__(self, settings: Settings):
        self.index_db_path = settings.PATENT_INDEX_DB_PATH
        self.patent_bulk_dir = settings.PATENT_BULK_DIR_PATH
        self.upstream_url = settings.UPSTREAM_SERVER_URL
        self.upstream_timeout = settings.UPSTREAM_TIMEOUT
        self.upstream_enabled = settings.UPSTREAM_ENABLED

        self._validate_config()

        try:
            self.db_connection = sqlite3.connect(self.index_db_path)
        except sqlite3.Error as e:
            raise RuntimeError(f"Failed to connect to patent database: {e}")

    def _validate_config(self):
        """Validate patent retriever configuration."""
        if not self.index_db_path:
            raise RuntimeError("PATENT_INDEX_DB_PATH not configured")

        if not os.path.exists(self.index_db_path):
            raise RuntimeError(f"Patent database file not found: {self.index_db_path}")

        if self.patent_bulk_dir and not os.path.exists(self.patent_bulk_dir):
            raise RuntimeError(f"Patent bulk directory not found: {self.patent_bulk_dir}")

    def _has_patent_index_table(self) -> bool:
        """Check if the patent_index table exists in the database."""
        cursor = self.db_connection.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='patent_index'"
        )
        return cursor.fetchone() is not None

    def _lookup_patent_metadata(self, patent_id: str) -> Optional[Dict[str, Any]]:
        """
        Look up patent metadata from the database.
        Returns dict with archive_file, offset, size, doc_type, kind_code, year or None.
        """
        if not self._has_patent_index_table():
            return None

        cursor = self.db_connection.cursor()
        cursor.execute(
            "SELECT archive_file, offset, size, doc_type, kind_code, year "
            "FROM patent_index WHERE patent_id = ?",
            (patent_id,)
        )
        result = cursor.fetchone()

        if result is None:
            return None

        return {
            "patent_id": patent_id,
            "archive_file": result[0],
            "offset": result[1],
            "size": result[2],
            "doc_type": result[3],
            "kind_code": result[4],
            "year": result[5],
        }

    def _get_from_local(self, patent_id: str) -> Optional[bytes]:
        """
        Retrieve patent XML from local ZIP archive.

        Opens the ZIP, reads the inner XML file, seeks to the byte offset,
        and reads the patent's XML block.
        """
        metadata = self._lookup_patent_metadata(patent_id)
        if metadata is None:
            return None

        if not self.patent_bulk_dir:
            return None

        zip_file_path = os.path.join(self.patent_bulk_dir, metadata["archive_file"])

        if not os.path.exists(zip_file_path):
            logger.debug(f"ZIP file not available locally: {zip_file_path}")
            return None

        try:
            with zipfile.ZipFile(zip_file_path, 'r') as zf:
                # Each USPTO ZIP has one inner XML file
                xml_names = [n for n in zf.namelist() if n.lower().endswith('.xml')]
                if not xml_names:
                    logger.warning(f"No XML file found in {zip_file_path}")
                    return None

                with zf.open(xml_names[0]) as xml_file:
                    xml_file.seek(metadata["offset"])
                    return xml_file.read(metadata["size"])

        except (zipfile.BadZipFile, OSError) as e:
            logger.warning(f"Error reading ZIP file {zip_file_path}: {e}")
            return None

    def _get_from_upstream(self, patent_id: str) -> Optional[bytes]:
        """Attempt to retrieve patent from upstream server."""
        if not self.upstream_url or not self.upstream_enabled:
            return None

        try:
            with httpx.Client(timeout=self.upstream_timeout) as client:
                response = client.get(f"{self.upstream_url}/patent/{patent_id}")
                if response.status_code == 200:
                    return response.content
                elif response.status_code == 404:
                    return None
                else:
                    logger.warning(
                        f"Upstream returned status {response.status_code} for patent {patent_id}"
                    )
                    return None
        except httpx.TimeoutException:
            logger.warning(f"Upstream timeout for patent {patent_id}")
            return None
        except httpx.RequestError as e:
            logger.warning(f"Upstream request error for patent {patent_id}: {e}")
            return None

    def _get_info_from_upstream(self, patent_id: str) -> Optional[Dict[str, Any]]:
        """Get patent metadata from upstream server's /info endpoint."""
        if not self.upstream_url or not self.upstream_enabled:
            return None

        try:
            with httpx.Client(timeout=self.upstream_timeout) as client:
                response = client.get(f"{self.upstream_url}/patent/{patent_id}/info")
                if response.status_code == 200:
                    return response.json()
                return None
        except (httpx.TimeoutException, httpx.RequestError) as e:
            logger.warning(f"Upstream info request error for patent {patent_id}: {e}")
            return None

    def get_patent_by_id(self, patent_id: str) -> Dict[str, Any]:
        """
        Get patent XML by ID.

        Returns dict with:
        - On success: content, content_type, error (None), patent_id, kind_code, doc_type, year, source
        - On error: content (None), content_type (None), error (str)
        """
        bare_id, requested_kind = parse_patent_id(patent_id)

        metadata = self._lookup_patent_metadata(bare_id)

        def success_response(content: bytes, source: str) -> Dict[str, Any]:
            meta = metadata or {}
            return {
                "content": content,
                "content_type": "application/xml",
                "error": None,
                "patent_id": bare_id,
                "kind_code": meta.get("kind_code") or requested_kind,
                "doc_type": meta.get("doc_type", "unknown"),
                "year": meta.get("year"),
                "source": source,
            }

        # Try local storage
        result = self._get_from_local(bare_id)
        if result is not None:
            return success_response(result, "local")

        # Try upstream
        result = self._get_from_upstream(bare_id)
        if result is not None:
            return success_response(result, "upstream")

        return {"content": None, "content_type": None, "error": "not_found"}

    def get_patent_info(self, patent_id: str) -> Optional[Dict[str, Any]]:
        """
        Get patent metadata without retrieving content.

        Checks local database first, then upstream.
        """
        bare_id, requested_kind = parse_patent_id(patent_id)

        metadata = self._lookup_patent_metadata(bare_id)

        if metadata is not None:
            # Check if ZIP file is locally available
            locally_available = False
            if self.patent_bulk_dir:
                zip_path = os.path.join(self.patent_bulk_dir, metadata["archive_file"])
                locally_available = os.path.exists(zip_path)

            return {
                "patent_id": bare_id,
                "kind_code": metadata["kind_code"],
                "doc_type": metadata["doc_type"],
                "size_bytes": metadata["size"],
                "year": metadata["year"],
                "locally_available": locally_available,
                "source": "local",
            }

        # Try upstream
        upstream_info = self._get_info_from_upstream(bare_id)
        if upstream_info is not None:
            upstream_info["source"] = "upstream"
            upstream_info["locally_available"] = False
            return upstream_info

        return None
