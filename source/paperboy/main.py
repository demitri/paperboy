from enum import Enum
from typing import Optional

from fastapi import FastAPI, HTTPException, Response, Request, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .config import Settings
from .retriever import PaperRetriever, RetrievalError


class PaperFormat(str, Enum):
    """Supported paper format filters."""
    pdf = "pdf"
    source = "source"
    preferred = "preferred"

# Initialize settings and retriever with error handling
try:
    settings = Settings()
    retriever = PaperRetriever(settings)
    startup_error = None
except RetrievalError as e:
    startup_error = str(e)
    retriever = None
except Exception as e:
    startup_error = f"Configuration error: {e}"
    retriever = None

app = FastAPI(
    title="Paperboy",
    description="""
## arXiv Paper Retrieval API

Paperboy retrieves academic papers from arXiv bulk tar archives using SQLite indexing for instant access.

### For AI Agents

**Primary endpoint:** `GET /paper/{paper_id}` - Returns raw paper content with correct Content-Type header.

**Metadata endpoint:** `GET /paper/{paper_id}/info` - Get paper metadata (format, size) before downloading.

**Paper ID formats accepted:**
- `1501.00963` - Modern arXiv ID
- `arXiv:1501.00963v3` - With prefix and version (version is respected)
- `astro-ph/0412561` or `astro-ph0412561` - Old category format
- `https://arxiv.org/abs/1501.00963` - Full arXiv URL

**Format selection:** Use `?format=pdf`, `?format=source`, or `?format=preferred` (default).

**Version handling:** Specifying a version (e.g., `v2`) requires exact match - returns 404 if not found.

**Response Content-Types:**
- `application/pdf` for PDF files
- `application/gzip` for gzip-compressed LaTeX source
- `application/x-tar` for tar archives

**Error handling:**
- `404`: Paper not found, version not found, or requested format unavailable
- `500`: Service misconfiguration

### Architecture
Papers are retrieved from: cache (if enabled) → local tar archives → upstream server (if configured).
""",
    version="1.0.0"
)

templates = Jinja2Templates(directory="templates")


@app.get("/", response_class=HTMLResponse, tags=["Human Interface"])
async def root(request: Request):
    """
    HTML search form for human users.

    **AI agents should use `GET /paper/{paper_id}` instead.**
    """
    return HTMLResponse(content="""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Paperboy</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            max-width: 800px;
            margin: 0 auto;
            padding: 20px;
            background-color: #f5f5f5;
        }
        .container {
            background-color: white;
            padding: 30px;
            border-radius: 8px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }
        h1 {
            color: #333;
            text-align: center;
            margin-bottom: 30px;
        }
        .form-group {
            margin-bottom: 20px;
        }
        label {
            display: block;
            margin-bottom: 5px;
            font-weight: bold;
            color: #555;
        }
        input[type="text"] {
            width: 100%;
            padding: 12px;
            border: 2px solid #ddd;
            border-radius: 4px;
            font-size: 16px;
            box-sizing: border-box;
        }
        input[type="text"]:focus {
            border-color: #4CAF50;
            outline: none;
        }
        button {
            background-color: #4CAF50;
            color: white;
            padding: 12px 24px;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            font-size: 16px;
            width: 100%;
        }
        button:hover {
            background-color: #45a049;
        }
        .info {
            background-color: #e7f3ff;
            border: 1px solid #b3d7ff;
            border-radius: 4px;
            padding: 15px;
            margin-bottom: 20px;
        }
        .examples {
            background-color: #f9f9f9;
            border: 1px solid #ddd;
            border-radius: 4px;
            padding: 15px;
            margin-top: 20px;
        }
        .examples h3 {
            margin-top: 0;
            color: #555;
        }
        .examples code {
            background-color: #f0f0f0;
            padding: 2px 4px;
            border-radius: 2px;
            font-family: monospace;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>Paperboy</h1>
        
        <div class="info">
            <strong>Note:</strong> This service extracts LaTeX source code and PDF files from arXiv papers. 
            Enter a valid arXiv paper ID below to download the corresponding file.
        </div>
        
        <form action="/download" method="post">
            <div class="form-group">
                <label for="paper_id">arXiv Paper ID:</label>
                <input type="text" id="paper_id" name="paper_id"
                       placeholder="e.g., arXiv:1501.00963v3, 2103.06497, astro-ph/9205002"
                       required>
            </div>
            <button type="submit">Download Paper</button>
        </form>

        <div class="examples">
            <h3>Accepted Formats:</h3>
            <ul>
                <li><code>arXiv:1501.00963v3</code> - With prefix and version</li>
                <li><code>1501.00963</code> - Just the ID</li>
                <li><code>astro-ph/0412561</code> - Old format with slash</li>
                <li><code>astro-ph0412561</code> - Old format without slash</li>
                <li><code>https://arxiv.org/abs/1501.00963</code> - Full URL</li>
            </ul>
        </div>
    </div>
</body>
</html>
    """)


@app.post("/download", tags=["Human Interface"])
async def download_paper(paper_id: str = Form(...)):
    """
    Form submission handler for human users. Returns file as attachment.

    **AI agents should use `GET /paper/{paper_id}` instead.**
    """
    # Check for startup errors
    if startup_error:
        return HTMLResponse(content=f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Service Configuration Error</title>
    <style>
        body {{
            font-family: Arial, sans-serif;
            max-width: 600px;
            margin: 0 auto;
            padding: 20px;
            background-color: #f5f5f5;
        }}
        .container {{
            background-color: white;
            padding: 30px;
            border-radius: 8px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            text-align: center;
        }}
        .error {{
            color: #d32f2f;
            font-size: 16px;
            margin-bottom: 20px;
            background-color: #ffebee;
            padding: 15px;
            border-radius: 4px;
            border-left: 4px solid #d32f2f;
        }}
        a {{
            color: #4CAF50;
            text-decoration: none;
        }}
        a:hover {{
            text-decoration: underline;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>Service Configuration Error</h1>
        <div class="error">
            {startup_error}
        </div>
        <p>Please check the service configuration and try again.</p>
        <p><a href="/">← Back to search</a></p>
    </div>
</body>
</html>
        """, status_code=500)
    
    try:
        content, content_type, error_reason = retriever.get_source_by_id(paper_id)

        if content is None:
            # Get detailed error information
            error_type, error_message = retriever.get_detailed_error(paper_id)
            if error_reason == "version_not_found":
                error_type = "version_not_found"
                error_message = f"Requested version of paper '{paper_id}' not found."

            return HTMLResponse(content=f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Paper Retrieval Error</title>
    <style>
        body {{
            font-family: Arial, sans-serif;
            max-width: 600px;
            margin: 0 auto;
            padding: 20px;
            background-color: #f5f5f5;
        }}
        .container {{
            background-color: white;
            padding: 30px;
            border-radius: 8px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            text-align: center;
        }}
        .error {{
            color: #d32f2f;
            font-size: 16px;
            margin-bottom: 20px;
            background-color: #ffebee;
            padding: 15px;
            border-radius: 4px;
            border-left: 4px solid #d32f2f;
            text-align: left;
        }}
        .error-type {{
            font-weight: bold;
            margin-bottom: 10px;
            text-transform: capitalize;
        }}
        a {{
            color: #4CAF50;
            text-decoration: none;
        }}
        a:hover {{
            text-decoration: underline;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>Paper Retrieval Error</h1>
        <div class="error">
            <div class="error-type">{error_type.replace('_', ' ').title()}</div>
            {error_message}
        </div>
        <p><a href="/">← Back to search</a></p>
    </div>
</body>
</html>
            """, status_code=404)
        
    except RetrievalError as e:
        return HTMLResponse(content=f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Retrieval Error</title>
    <style>
        body {{
            font-family: Arial, sans-serif;
            max-width: 600px;
            margin: 0 auto;
            padding: 20px;
            background-color: #f5f5f5;
        }}
        .container {{
            background-color: white;
            padding: 30px;
            border-radius: 8px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            text-align: center;
        }}
        .error {{
            color: #d32f2f;
            font-size: 16px;
            margin-bottom: 20px;
            background-color: #ffebee;
            padding: 15px;
            border-radius: 4px;
            border-left: 4px solid #d32f2f;
        }}
        a {{
            color: #4CAF50;
            text-decoration: none;
        }}
        a:hover {{
            text-decoration: underline;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>System Error</h1>
        <div class="error">
            {str(e)}
        </div>
        <p><a href="/">← Back to search</a></p>
    </div>
</body>
</html>
        """, status_code=500)
    
    # Determine the appropriate filename based on content type
    if content_type == "application/pdf":
        filename = f"{paper_id}.pdf"
    elif content_type == "application/x-tar":
        filename = f"{paper_id}.tar"
    else:
        filename = f"{paper_id}.gz"

    return Response(
        content=content,
        media_type=content_type,
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@app.get("/health", tags=["Status"])
async def health():
    """
    Health check endpoint for monitoring and load balancers.

    **Response fields:**
    - `status`: "healthy" or "unhealthy"
    - `startup_error`: Error message if service failed to start, null otherwise
    - `upstream_configured`: Whether an upstream fallback server is configured
    - `upstream_enabled`: Whether upstream fallback is enabled
    - `cache_configured`: Whether paper caching is enabled
    """
    import os
    return {
        "status": "healthy" if retriever else "unhealthy",
        "startup_error": startup_error,
        "upstream_configured": bool(settings.UPSTREAM_SERVER_URL),
        "upstream_enabled": settings.UPSTREAM_ENABLED,
        "cache_configured": bool(settings.CACHE_DIR_PATH),
    }


@app.get("/debug/config", tags=["Status"])
async def debug_config():
    """
    Debug endpoint showing full service configuration.

    **Response includes:**
    - All configuration paths and settings
    - Whether required files/directories exist
    - Cache statistics (if caching enabled): size, utilization, paper count
    """
    import os
    config = {
        "INDEX_DB_PATH": settings.INDEX_DB_PATH,
        "TAR_DIR_PATH": settings.TAR_DIR_PATH,
        "UPSTREAM_SERVER_URL": settings.UPSTREAM_SERVER_URL,
        "UPSTREAM_TIMEOUT": settings.UPSTREAM_TIMEOUT,
        "UPSTREAM_ENABLED": settings.UPSTREAM_ENABLED,
        "CACHE_DIR_PATH": settings.CACHE_DIR_PATH,
        "CACHE_MAX_SIZE_GB": settings.CACHE_MAX_SIZE_GB,
        "db_exists": os.path.exists(settings.INDEX_DB_PATH),
        "tar_dir_exists": os.path.exists(settings.TAR_DIR_PATH),
        "working_directory": os.getcwd()
    }

    # Add cache stats if cache is configured
    if retriever and retriever.cache:
        config["cache_stats"] = retriever.cache.get_stats()

    return config


@app.get("/paper/{paper_id:path}/info", tags=["Paper Retrieval"])
async def get_paper_info(paper_id: str):
    """
    Get metadata about a paper without downloading its content.

    Use this endpoint to check paper availability and format before downloading.

    **Response fields:**
    - `paper_id`: The normalized paper ID stored in the database
    - `requested_version`: Version number if you requested a specific version
    - `file_type`: Raw type from database (pdf, gzip, tar, unknown)
    - `format`: Simplified format category (pdf, source, unknown)
    - `size_bytes`: File size in bytes
    - `year`: Publication year
    - `locally_available`: Whether the paper is stored locally
    - `upstream_configured`: Whether upstream fallback is available

    **Example:**
    ```
    GET /paper/2103.06497/info
    ```
    """
    info = retriever.get_paper_info(paper_id)

    if info is None:
        raise HTTPException(
            status_code=404,
            detail=f"Paper with ID '{paper_id}' not found."
        )

    return info


@app.get("/paper/{paper_id:path}", tags=["Paper Retrieval"])
async def get_paper(
    paper_id: str,
    format: Optional[PaperFormat] = Query(
        default=None,
        description="Filter by format: 'pdf' (PDF only), 'source' (LaTeX source only), 'preferred' (return whatever is available)"
    )
):
    """
    Retrieve a paper by its arXiv ID. **This is the primary endpoint for AI agents.**

    **Paper ID formats accepted:**
    - `1501.00963` - Modern arXiv ID (YYMM.NNNNN)
    - `arXiv:1501.00963v3` - With prefix and version (returns specific version or 404)
    - `astro-ph/0412561` - Old format with category and slash
    - `astro-ph0412561` - Old format without slash
    - `https://arxiv.org/abs/1501.00963` - Full arXiv URL

    **Version handling:**
    - If you specify a version (e.g., `v2`), that exact version must exist or you get 404
    - If no version specified, returns the available version

    **Format parameter:**
    - `format=pdf` - Only return PDF, 404 if not available
    - `format=source` - Only return source (gzip/tar), 404 if not available
    - `format=preferred` - Return whatever is available (default)

    **Returns:**
    - Raw binary content with correct Content-Type header:
      - `application/pdf` for PDF files
      - `application/gzip` for gzip-compressed LaTeX source
      - `application/x-tar` for tar archives

    **Errors:**
    - `404`: Paper not found, version not found, or requested format unavailable

    **Examples:**
    ```
    GET /paper/2103.06497
    GET /paper/2103.06497?format=pdf
    GET /paper/2103.06497v2
    GET /paper/astro-ph/0412561?format=source
    ```
    """
    format_str = format.value if format else None
    content, content_type, error_reason = retriever.get_source_by_id(paper_id, format=format_str)

    if content is None:
        if error_reason == "version_not_found":
            raise HTTPException(
                status_code=404,
                detail=f"Requested version of paper '{paper_id}' not found."
            )
        elif error_reason == "format_unavailable":
            raise HTTPException(
                status_code=404,
                detail=f"Paper '{paper_id}' is not available in '{format_str}' format."
            )
        else:
            raise HTTPException(
                status_code=404,
                detail=f"Paper with ID '{paper_id}' not found."
            )

    return Response(content=content, media_type=content_type)