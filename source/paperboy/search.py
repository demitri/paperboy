"""
Typesense search client for paper full-text search.

Provides search functionality with faceted filtering, highlighting,
and pagination support.

Supports field-specific searches:
  - author:einstein     -> search authors field only
  - title:dark matter   -> search title field only
  - abstract:cosmology  -> search abstract field only
  - category:astro-ph   -> search categories field only
  - author:einstein relativity -> combined field + general search
"""

import logging
import re
from typing import Optional, Dict, Any, List, Tuple

import typesense
from typesense.exceptions import ObjectNotFound, RequestUnauthorized

from .config import Settings

logger = logging.getLogger(__name__)

# Field aliases for search
FIELD_ALIASES = {
    'author': 'authors',
    'authors': 'authors',
    'title': 'title',
    'abstract': 'abstract',
    'category': 'categories',
    'categories': 'categories',
    'cat': 'categories',
}


def parse_field_query(query: str) -> Tuple[Dict[str, str], str]:
    """
    Parse a query string for field-specific searches.

    Supports: field:value or field:"value with spaces"

    Returns:
        Tuple of (field_queries dict, remaining general query)

    Examples:
        "author:einstein" -> ({"authors": "einstein"}, "")
        "author:einstein dark matter" -> ({"authors": "einstein"}, "dark matter")
        'title:"dark matter" cosmology' -> ({"title": "dark matter"}, "cosmology")
    """
    field_queries = {}
    remaining = query

    # Pattern to match field:value or field:"quoted value"
    pattern = r'(\w+):(?:"([^"]+)"|(\S+))'

    for match in re.finditer(pattern, query):
        field_name = match.group(1).lower()
        value = match.group(2) or match.group(3)  # Quoted or unquoted

        if field_name in FIELD_ALIASES:
            field_queries[FIELD_ALIASES[field_name]] = value
            # Remove this field:value from remaining query
            remaining = remaining.replace(match.group(0), '', 1)

    # Clean up remaining query
    remaining = ' '.join(remaining.split())

    return field_queries, remaining


class SearchClient:
    """Client for searching papers via Typesense."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.collection_name = settings.TYPESENSE_COLLECTION
        self.client: Optional[typesense.Client] = None
        self._connected = False

        if settings.TYPESENSE_ENABLED and settings.TYPESENSE_API_KEY:
            self._init_client()

    def _init_client(self) -> None:
        """Initialize the Typesense client."""
        try:
            self.client = typesense.Client({
                "nodes": [{
                    "host": self.settings.TYPESENSE_HOST,
                    "port": str(self.settings.TYPESENSE_PORT),
                    "protocol": self.settings.TYPESENSE_PROTOCOL,
                }],
                "api_key": self.settings.TYPESENSE_API_KEY,
                "connection_timeout_seconds": 5,
            })
            # Test connection by listing collections
            self.client.collections.retrieve()
            self._connected = True
            logger.info(f"Connected to Typesense at {self.settings.TYPESENSE_HOST}:{self.settings.TYPESENSE_PORT}")
        except Exception as e:
            logger.warning(f"Failed to connect to Typesense: {e}")
            self._connected = False

    @property
    def is_available(self) -> bool:
        """Check if search is available."""
        return self._connected and self.client is not None

    def search(
        self,
        query: str,
        category: Optional[str] = None,
        year_min: Optional[int] = None,
        year_max: Optional[int] = None,
        file_type: Optional[str] = None,
        page: int = 1,
        per_page: int = 20,
    ) -> Dict[str, Any]:
        """
        Search for papers.

        Args:
            query: Search query string
            category: Filter by category (e.g., "astro-ph", "cs.AI")
            year_min: Minimum year filter
            year_max: Maximum year filter
            file_type: Filter by file type ("pdf" or "source")
            page: Page number (1-indexed)
            per_page: Results per page (max 100)

        Returns:
            Dict with hits, facets, and pagination info
        """
        if not self.is_available:
            return {
                "error": "Search is not available",
                "query": query,
                "found": 0,
                "hits": [],
            }

        # Clamp per_page to reasonable bounds
        per_page = max(1, min(per_page, 100))

        # Parse field-specific queries (e.g., "author:einstein")
        field_queries, general_query = parse_field_query(query)

        # Build filter string
        filters = []
        if category:
            # Match category prefix (e.g., "astro-ph" matches "astro-ph.GA")
            filters.append(f"categories:=[{category}] || primary_category:={category} || categories:=[{category}.*]")
        if year_min:
            filters.append(f"year:>={year_min}")
        if year_max:
            filters.append(f"year:<={year_max}")
        if file_type:
            if file_type == "source":
                filters.append("file_type:=[gzip, tar]")
            else:
                filters.append(f"file_type:={file_type}")

        filter_by = " && ".join(filters) if filters else ""

        # Determine query_by based on field-specific searches
        if field_queries and not general_query:
            # Only field-specific search - search just those fields
            query_fields = list(field_queries.keys())
            search_query = ' '.join(field_queries.values())
            query_by = ','.join(query_fields)
            query_by_weights = ','.join(['1'] * len(query_fields))
        elif field_queries and general_query:
            # Mixed: field-specific + general search
            # For now, combine into a single search with adjusted weights
            search_query = ' '.join(list(field_queries.values()) + [general_query])
            query_by = "title,authors,abstract,categories"
            query_by_weights = "4,2,1,1"
        else:
            # Standard full-text search
            search_query = query
            query_by = "title,authors,abstract,categories"
            query_by_weights = "4,2,1,1"  # Weight title most heavily

        # Build search parameters
        search_params = {
            "q": search_query,
            "query_by": query_by,
            "query_by_weights": query_by_weights,
            "page": page,
            "per_page": per_page,
            "highlight_full_fields": "title,abstract",
            "highlight_start_tag": "<mark>",
            "highlight_end_tag": "</mark>",
            "facet_by": "primary_category,year,file_type",
            "max_facet_values": 20,
            "num_typos": 2,
            "typo_tokens_threshold": 3,
        }

        if filter_by:
            search_params["filter_by"] = filter_by

        try:
            result = self.client.collections[self.collection_name].documents.search(search_params)

            # Format response
            hits = []
            for hit in result.get("hits", []):
                doc = hit["document"]
                highlights = hit.get("highlights", [])

                # Build highlights dict
                highlight_dict = {}
                for h in highlights:
                    field = h.get("field")
                    snippet = h.get("snippet") or h.get("value")
                    if field and snippet:
                        highlight_dict[field] = snippet

                hits.append({
                    "paper_id": doc.get("paper_id"),
                    "title": doc.get("title"),
                    "authors": doc.get("authors"),
                    "abstract": doc.get("abstract", "")[:500] + "..." if len(doc.get("abstract", "")) > 500 else doc.get("abstract", ""),
                    "categories": doc.get("categories", []),
                    "primary_category": doc.get("primary_category"),
                    "year": doc.get("year"),
                    "file_type": doc.get("file_type"),
                    "doi": doc.get("doi"),
                    "journal_ref": doc.get("journal_ref"),
                    "highlights": highlight_dict,
                })

            # Format facets
            facets = {}
            for facet in result.get("facet_counts", []):
                field = facet.get("field_name")
                counts = facet.get("counts", [])
                facets[field] = [
                    {"value": c["value"], "count": c["count"]}
                    for c in counts
                ]

            return {
                "query": query,
                "found": result.get("found", 0),
                "page": page,
                "per_page": per_page,
                "total_pages": (result.get("found", 0) + per_page - 1) // per_page,
                "hits": hits,
                "facets": facets,
                "search_time_ms": result.get("search_time_ms"),
            }

        except ObjectNotFound:
            logger.error(f"Collection '{self.collection_name}' not found")
            return {
                "error": "Search index not found. Run sync_typesense.py to create it.",
                "query": query,
                "found": 0,
                "hits": [],
            }
        except RequestUnauthorized:
            logger.error("Typesense authentication failed")
            return {
                "error": "Search authentication failed",
                "query": query,
                "found": 0,
                "hits": [],
            }
        except Exception as e:
            logger.error(f"Search error: {e}")
            return {
                "error": f"Search error: {str(e)}",
                "query": query,
                "found": 0,
                "hits": [],
            }

    def suggest(self, query: str, limit: int = 5) -> List[str]:
        """
        Get autocomplete suggestions for a query.

        Args:
            query: Partial query string
            limit: Maximum number of suggestions

        Returns:
            List of suggested completions
        """
        if not self.is_available or len(query) < 2:
            return []

        try:
            result = self.client.collections[self.collection_name].documents.search({
                "q": query,
                "query_by": "title",
                "per_page": limit,
                "prefix": True,
                "num_typos": 1,
            })

            suggestions = []
            for hit in result.get("hits", []):
                title = hit["document"].get("title", "")
                if title and title not in suggestions:
                    suggestions.append(title)

            return suggestions[:limit]

        except Exception as e:
            logger.warning(f"Suggestion error: {e}")
            return []

    def get_stats(self) -> Dict[str, Any]:
        """Get search index statistics."""
        if not self.is_available:
            return {"available": False, "error": "Not connected"}

        try:
            info = self.client.collections[self.collection_name].retrieve()
            return {
                "available": True,
                "collection": info["name"],
                "num_documents": info["num_documents"],
                "fields": len(info["fields"]),
            }
        except ObjectNotFound:
            return {"available": False, "error": "Collection not found"}
        except Exception as e:
            return {"available": False, "error": str(e)}
