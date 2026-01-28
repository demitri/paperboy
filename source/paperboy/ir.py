"""IR (Intermediate Representation) package generation for arXiv papers.

This module provides functionality to generate IR packages from raw arXiv source.
IR packages contain:
- LaTeXML XML output (parsed LaTeX)
- Source files
- Manifest with metadata

The IR package is the canonical format for downstream processing (chunking, indexing).
"""

import gzip
import io
import logging
import tarfile
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)


def extract_latex_from_content(content: bytes) -> Tuple[Dict[str, str], Optional[str]]:
    """Extract LaTeX files from raw arXiv content.

    Handles:
    - Single gzipped .tex file
    - Gzipped tar archive with multiple files

    Args:
        content: Raw bytes (gzip or tar.gz)

    Returns:
        Tuple of (latex_files dict, error message or None)
    """
    latex_files: Dict[str, str] = {}

    # Try to decompress gzip
    try:
        decompressed = gzip.decompress(content)
    except gzip.BadGzipFile:
        return {}, "Content is not valid gzip"

    # Check if it's a PDF (not LaTeX source)
    if decompressed[:4] == b"%PDF":
        return {}, "Content is PDF, not LaTeX source"

    # Check if it's a tar archive
    if _is_tar_archive(decompressed):
        return _extract_tar(decompressed)

    # Single file - assume it's the main .tex
    try:
        content_str = decompressed.decode("utf-8", errors="replace")
        if "\\" not in content_str[:1000]:
            return {}, "Content does not appear to be LaTeX"
        latex_files["main.tex"] = content_str
        return latex_files, None
    except Exception as e:
        return {}, f"Failed to decode content: {e}"


def _is_tar_archive(data: bytes) -> bool:
    """Check if data is a tar archive."""
    if len(data) > 262:
        magic = data[257:262]
        return magic == b"ustar" or magic[:5] == b"ustar"
    return False


def _extract_tar(data: bytes) -> Tuple[Dict[str, str], Optional[str]]:
    """Extract text files from tar archive."""
    latex_files: Dict[str, str] = {}
    text_extensions = {".tex", ".bbl", ".bib", ".sty", ".cls", ".txt", ".bst", ".cfg"}

    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as tar:
            for member in tar.getmembers():
                if not member.isfile():
                    continue

                name_lower = member.name.lower()
                if any(name_lower.endswith(ext) for ext in text_extensions):
                    try:
                        f = tar.extractfile(member)
                        if f is not None:
                            content = f.read().decode("utf-8", errors="replace")
                            latex_files[member.name] = content
                    except Exception as e:
                        logger.warning(f"Failed to extract {member.name}: {e}")

        return latex_files, None
    except tarfile.TarError as e:
        return {}, f"Failed to extract tar: {e}"


def identify_main_tex(latex_files: Dict[str, str]) -> Optional[str]:
    """Identify the main .tex file from a collection of files.

    Heuristics:
    1. File with both \\documentclass and \\begin{document}
    2. Prefer files named main.tex, paper.tex, ms.tex, article.tex
    3. Fall back to any .tex with \\begin{document}
    """
    preferred_names = {"main.tex", "paper.tex", "ms.tex", "article.tex"}
    candidates = []

    for filename, content in latex_files.items():
        if not filename.lower().endswith(".tex"):
            continue

        has_documentclass = "\\documentclass" in content
        has_begin_document = "\\begin{document}" in content

        if has_documentclass and has_begin_document:
            priority = 0
            basename = filename.lower().split("/")[-1]
            if basename in preferred_names:
                priority = -1
            candidates.append((priority, filename))
        elif has_begin_document:
            candidates.append((1, filename))

    if not candidates:
        for filename in latex_files:
            if filename.lower().endswith(".tex"):
                return filename
        return None

    candidates.sort(key=lambda x: x[0])
    return candidates[0][1]


def generate_ir_package(
    paper_id: str,
    content: bytes,
    profile: str = "text-only",
) -> Tuple[Optional[bytes], Optional[str]]:
    """Generate an IR package from raw arXiv source content.

    Args:
        paper_id: arXiv paper identifier
        content: Raw source content (gzip or tar.gz)
        profile: IR profile ("text-only" or "full")

    Returns:
        Tuple of (IR package bytes, error message or None)
    """
    try:
        from arxiv_src_ir import IRBuilder, IRProfile, LatexmlNotFoundError
    except ImportError:
        return None, "arxiv_src_ir package not installed. Install with: pip install arxiv-src-ir"

    # Extract LaTeX files
    latex_files, error = extract_latex_from_content(content)
    if error:
        return None, error

    if not latex_files:
        return None, "No LaTeX files found in content"

    # Identify main tex file
    main_tex_filename = identify_main_tex(latex_files)

    # Build IR package
    try:
        ir_profile = IRProfile.FULL if profile == "full" else IRProfile.TEXT_ONLY
        builder = IRBuilder(profile=ir_profile)

        result = builder.build_from_source_files(
            paper_id=paper_id,
            latex_files=latex_files,
            main_tex_filename=main_tex_filename,
        )

        if result.package_bytes:
            return result.package_bytes, None
        else:
            return None, "IR builder produced no output"

    except LatexmlNotFoundError as e:
        logger.error(f"LaTeXML not configured: {e}")
        return None, f"LaTeXML not configured. Set LATEXML_BIN environment variable to the path of the latexml binary."

    except Exception as e:
        logger.exception(f"Failed to generate IR package for {paper_id}")
        return None, f"IR generation failed: {e}"
