from enum import Enum
from typing import Optional

from fastapi import FastAPI, HTTPException, Response, Request, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .config import Settings
from .ir_cache import IRCache
from .patent_retriever import PatentRetriever, normalize_patent_id
from .retriever import PaperRetriever, RetrievalError, get_expected_tar_pattern
from .search import SearchClient


class PaperFormat(str, Enum):
    """Supported paper format filters."""
    pdf = "pdf"
    source = "source"
    preferred = "preferred"

# Initialize settings and retriever with error handling
try:
    settings = Settings()
    retriever = PaperRetriever(settings)
    search_client = SearchClient(settings)
    startup_error = None
except RetrievalError as e:
    startup_error = str(e)
    retriever = None
    search_client = None
except Exception as e:
    startup_error = f"Configuration error: {e}"
    retriever = None
    search_client = None

# Initialize IR cache if configured
ir_cache: Optional[IRCache] = None
if settings.IR_CACHE_DIR_PATH:
    ir_cache = IRCache(settings.IR_CACHE_DIR_PATH, settings.IR_CACHE_MAX_SIZE_GB)

# Initialize patent retriever if configured (requires both DB path and bulk dir)
patent_retriever: Optional[PatentRetriever] = None
if settings.PATENT_INDEX_DB_PATH and settings.PATENT_BULK_DIR_PATH:
    try:
        patent_retriever = PatentRetriever(settings)
    except Exception as e:
        import logging as _logging
        _logging.getLogger(__name__).warning(f"Patent retriever not available: {e}")

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

    **AI agents should use `GET /paper/{paper_id}` or `GET /search` instead.**
    """
    search_enabled = search_client.is_available if search_client else False
    return HTMLResponse(content=f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Paperboy - arXiv Paper Search</title>
    <style>
        * {{ box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            max-width: 900px;
            margin: 0 auto;
            padding: 20px;
            background-color: #f5f5f5;
            color: #333;
        }}
        .container {{
            background-color: white;
            padding: 30px;
            border-radius: 8px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }}
        h1 {{
            color: #333;
            text-align: center;
            margin-bottom: 10px;
        }}
        .subtitle {{
            text-align: center;
            color: #666;
            margin-bottom: 30px;
        }}
        .tabs {{
            display: flex;
            border-bottom: 2px solid #ddd;
            margin-bottom: 20px;
        }}
        .tab {{
            padding: 12px 24px;
            cursor: pointer;
            border: none;
            background: none;
            font-size: 16px;
            color: #666;
            border-bottom: 2px solid transparent;
            margin-bottom: -2px;
        }}
        .tab:hover {{ color: #333; }}
        .tab.active {{
            color: #4CAF50;
            border-bottom-color: #4CAF50;
            font-weight: 600;
        }}
        .tab-content {{ display: none; }}
        .tab-content.active {{ display: block; }}
        .search-box {{
            display: flex;
            gap: 10px;
            margin-bottom: 20px;
        }}
        .search-box input {{
            flex: 1;
            padding: 14px 16px;
            border: 2px solid #ddd;
            border-radius: 6px;
            font-size: 16px;
        }}
        .search-box input:focus {{
            border-color: #4CAF50;
            outline: none;
        }}
        .search-box button {{
            padding: 14px 28px;
            background-color: #4CAF50;
            color: white;
            border: none;
            border-radius: 6px;
            cursor: pointer;
            font-size: 16px;
            font-weight: 600;
        }}
        .search-box button:hover {{ background-color: #45a049; }}
        .search-box button:disabled {{ background-color: #9e9e9e; cursor: not-allowed; }}
        .filters {{
            display: flex;
            gap: 15px;
            margin-bottom: 20px;
            flex-wrap: wrap;
        }}
        .filter-group {{
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        .filter-group label {{
            font-size: 14px;
            color: #666;
        }}
        .filter-group select, .filter-group input {{
            padding: 8px 12px;
            border: 1px solid #ddd;
            border-radius: 4px;
            font-size: 14px;
        }}
        .results-info {{
            padding: 10px 0;
            color: #666;
            font-size: 14px;
            border-bottom: 1px solid #eee;
            margin-bottom: 15px;
        }}
        .result-card {{
            padding: 20px;
            border: 1px solid #e0e0e0;
            border-radius: 8px;
            margin-bottom: 15px;
            transition: box-shadow 0.2s;
        }}
        .result-card:hover {{
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        }}
        .result-title {{
            font-size: 18px;
            font-weight: 600;
            color: #1a0dab;
            margin-bottom: 8px;
            cursor: pointer;
        }}
        .result-title:hover {{ text-decoration: underline; }}
        .result-meta {{
            font-size: 13px;
            color: #666;
            margin-bottom: 10px;
        }}
        .result-meta span {{
            margin-right: 15px;
        }}
        .result-abstract {{
            font-size: 14px;
            color: #444;
            line-height: 1.5;
            cursor: pointer;
        }}
        .result-abstract:hover {{
            background-color: #f9f9f9;
        }}
        .result-abstract.expanded {{
            background-color: #fafafa;
            padding: 10px;
            border-radius: 4px;
            margin: 5px 0;
        }}
        .abstract-hint {{
            font-size: 12px;
            color: #999;
            font-style: italic;
        }}
        .result-categories {{
            margin-top: 10px;
        }}
        .category-tag {{
            display: inline-block;
            padding: 3px 8px;
            background-color: #e8f5e9;
            color: #2e7d32;
            border-radius: 4px;
            font-size: 12px;
            margin-right: 5px;
            margin-top: 5px;
        }}
        .download-btn {{
            display: inline-block;
            padding: 8px 16px;
            background-color: #4CAF50;
            color: white;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            font-size: 13px;
            margin-top: 10px;
        }}
        .download-btn:hover {{ background-color: #45a049; }}
        mark {{
            background-color: #fff59d;
            padding: 0 2px;
        }}
        .pagination {{
            display: flex;
            justify-content: center;
            gap: 10px;
            margin-top: 20px;
        }}
        .pagination button {{
            padding: 8px 16px;
            border: 1px solid #ddd;
            background: white;
            border-radius: 4px;
            cursor: pointer;
        }}
        .pagination button:hover {{ background-color: #f5f5f5; }}
        .pagination button:disabled {{ opacity: 0.5; cursor: not-allowed; }}
        .pagination .current {{ background-color: #4CAF50; color: white; border-color: #4CAF50; }}
        .error {{
            color: #d32f2f;
            background-color: #ffebee;
            padding: 15px;
            border-radius: 4px;
            border-left: 4px solid #d32f2f;
        }}
        .success {{
            color: #2e7d32;
            background-color: #e8f5e9;
            padding: 15px;
            border-radius: 4px;
            border-left: 4px solid #4CAF50;
        }}
        .loading {{
            display: inline-block;
            width: 16px;
            height: 16px;
            border: 2px solid #ffffff;
            border-radius: 50%;
            border-top-color: transparent;
            animation: spin 1s linear infinite;
            margin-right: 8px;
            vertical-align: middle;
        }}
        @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
        .no-search {{
            text-align: center;
            padding: 40px;
            color: #666;
        }}
        .hint {{
            background-color: #e3f2fd;
            border: 1px solid #90caf9;
            border-radius: 4px;
            padding: 15px;
            margin-top: 15px;
        }}
        .hint-title {{
            font-weight: bold;
            color: #1565c0;
            margin-bottom: 10px;
        }}
        .hint ul {{ margin: 10px 0; padding-left: 20px; }}
        .hint code {{
            background-color: #e8e8e8;
            padding: 2px 6px;
            border-radius: 3px;
            font-family: monospace;
        }}
        #searchResults, #downloadResult {{ margin-top: 20px; }}
        .keyboard-hint {{
            font-size: 12px;
            color: #999;
            text-align: center;
            margin-top: 15px;
        }}
        .search-syntax-hint {{
            font-size: 12px;
            color: #888;
            margin-bottom: 15px;
            min-height: 20px;
        }}
        .search-syntax-hint code {{
            background-color: #f0f0f0;
            padding: 2px 6px;
            border-radius: 3px;
            font-family: monospace;
            margin-right: 8px;
        }}
        .category-suggestions {{
            display: flex;
            flex-wrap: wrap;
            gap: 4px;
        }}
        .category-suggestions span {{
            background-color: #e8f5e9;
            color: #2e7d32;
            padding: 2px 8px;
            border-radius: 3px;
            font-size: 11px;
            cursor: pointer;
        }}
        .category-suggestions span:hover {{
            background-color: #c8e6c9;
        }}
        kbd {{
            background-color: #f0f0f0;
            border: 1px solid #ccc;
            border-radius: 3px;
            padding: 2px 6px;
            font-family: monospace;
            font-size: 11px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>Paperboy</h1>
        <p class="subtitle">Search and download arXiv papers</p>

        <div class="tabs">
            <button class="tab active" data-tab="search">Search Papers</button>
            <button class="tab" data-tab="download">Download by ID</button>
        </div>

        <!-- Search Tab -->
        <div id="searchTab" class="tab-content active">
            {'<div class="no-search"><p>Search is not available.</p><p>Typesense is not configured or running.</p></div>' if not search_enabled else '''
            <form id="searchForm">
                <div class="search-box">
                    <input type="text" id="searchQuery" placeholder="Search papers... (try author:name or title:words)" autofocus>
                    <button type="submit" id="searchBtn">Search</button>
                </div>
                <div class="search-syntax-hint">
                    Field search: <code>author:einstein</code> <code>title:relativity</code> <code>abstract:quantum</code> <code>category:hep-th</code>
                </div>
                <div class="filters">
                    <div class="filter-group">
                        <label for="categoryFilter">Category:</label>
                        <select id="categoryFilter">
                            <option value="">All</option>
                        </select>
                    </div>
                    <div class="filter-group">
                        <label for="yearMin">Year:</label>
                        <input type="number" id="yearMin" placeholder="From" style="width: 80px;">
                        <span>-</span>
                        <input type="number" id="yearMax" placeholder="To" style="width: 80px;">
                    </div>
                    <div class="filter-group">
                        <label for="formatFilter">Format:</label>
                        <select id="formatFilter">
                            <option value="">All</option>
                            <option value="pdf">PDF</option>
                            <option value="source">Source</option>
                        </select>
                    </div>
                </div>
            </form>
            <div id="searchResults"></div>
            <p class="keyboard-hint">Tip: Press <kbd>/</kbd> anywhere to jump to search box</p>
            '''}
        </div>

        <!-- Download Tab -->
        <div id="downloadTab" class="tab-content">
            <form id="downloadForm">
                <div class="search-box">
                    <input type="text" id="paper_id" placeholder="e.g., 2103.06497, arXiv:1501.00963v3, astro-ph/0412561">
                    <button type="submit" id="downloadBtn">Download</button>
                </div>
            </form>
            <div id="downloadResult"></div>
            <div class="hint" style="margin-top: 20px;">
                <div class="hint-title">Accepted ID Formats</div>
                <ul>
                    <li><code>2103.06497</code> - Modern arXiv ID</li>
                    <li><code>arXiv:1501.00963v3</code> - With prefix and version</li>
                    <li><code>astro-ph/0412561</code> - Old format with category</li>
                    <li><code>https://arxiv.org/abs/1501.00963</code> - Full URL</li>
                </ul>
            </div>
        </div>
    </div>

    <script>
        // Tab switching
        document.querySelectorAll('.tab').forEach(tab => {{
            tab.addEventListener('click', () => {{
                document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
                document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
                tab.classList.add('active');
                document.getElementById(tab.dataset.tab + 'Tab').classList.add('active');
            }});
        }});

        // Keyboard shortcut
        document.addEventListener('keydown', (e) => {{
            if (e.key === '/' && document.activeElement.tagName !== 'INPUT') {{
                e.preventDefault();
                const searchInput = document.getElementById('searchQuery');
                if (searchInput) searchInput.focus();
            }}
        }});

        // Load categories for filter and autocomplete
        let allCategories = [];
        const defaultHint = 'Field search: <code>author:einstein</code> <code>title:relativity</code> <code>abstract:quantum</code> <code>category:hep-th</code>';

        async function loadCategories() {{
            try {{
                const resp = await fetch('/paper/categories');
                const data = await resp.json();
                const select = document.getElementById('categoryFilter');
                if (data.all_categories) {{
                    allCategories = data.all_categories;
                    if (select) {{
                        allCategories.slice(0, 50).forEach(cat => {{
                            const opt = document.createElement('option');
                            opt.value = cat;
                            opt.textContent = cat;
                            select.appendChild(opt);
                        }});
                    }}
                }}
            }} catch(e) {{ console.log('Could not load categories'); }}
        }}
        loadCategories();

        // Category autocomplete in search box
        function updateCategoryHint() {{
            const input = document.getElementById('searchQuery');
            const hintDiv = document.querySelector('.search-syntax-hint');
            if (!input || !hintDiv) return;

            const value = input.value;
            // Check if user is typing a category: field
            const catMatch = value.match(/category:(\S*)$/i) || value.match(/cat:(\S*)$/i);

            if (catMatch) {{
                const partial = catMatch[1].toLowerCase();
                const matches = allCategories.filter(c => c.toLowerCase().startsWith(partial)).slice(0, 30);

                if (matches.length > 0) {{
                    hintDiv.innerHTML = '<div class="category-suggestions">' +
                        matches.map(c => `<span onclick="insertCategory('${{c}}')">${{c}}</span>`).join('') +
                        '</div>';
                    return;
                }}
            }}

            // Restore default hint
            if (hintDiv.innerHTML !== defaultHint) {{
                hintDiv.innerHTML = defaultHint;
            }}
        }}

        function insertCategory(cat) {{
            const input = document.getElementById('searchQuery');
            if (!input) return;
            // Replace the partial category with the full one
            input.value = input.value.replace(/category:\S*$/i, 'category:' + cat + ' ').replace(/cat:\S*$/i, 'category:' + cat + ' ');
            input.focus();
            updateCategoryHint();
            // Trigger search
            clearTimeout(searchTimeout);
            searchTimeout = setTimeout(() => {{ currentPage = 1; doSearch(); }}, 300);
        }}

        // Search functionality
        let currentPage = 1;
        let searchTimeout = null;
        const searchForm = document.getElementById('searchForm');
        const searchInput = document.getElementById('searchQuery');

        if (searchForm) {{
            searchForm.addEventListener('submit', (e) => {{
                e.preventDefault();
                currentPage = 1;
                doSearch();
            }});
        }}

        // Live search with debouncing
        if (searchInput) {{
            searchInput.addEventListener('input', () => {{
                updateCategoryHint();
                clearTimeout(searchTimeout);
                const query = searchInput.value.trim();
                if (query.length >= 2) {{
                    searchTimeout = setTimeout(() => {{
                        currentPage = 1;
                        doSearch();
                    }}, 300);
                }}
            }});
        }}

        async function doSearch(page = 1) {{
            const query = document.getElementById('searchQuery').value.trim();
            if (!query) return;

            const category = document.getElementById('categoryFilter').value;
            const yearMin = document.getElementById('yearMin').value;
            const yearMax = document.getElementById('yearMax').value;
            const format = document.getElementById('formatFilter').value;

            const searchBtn = document.getElementById('searchBtn');
            const resultsDiv = document.getElementById('searchResults');

            searchBtn.disabled = true;
            searchBtn.innerHTML = '<span class="loading"></span>Searching...';

            let url = `/search?q=${{encodeURIComponent(query)}}&page=${{page}}&per_page=20`;
            if (category) url += `&category=${{encodeURIComponent(category)}}`;
            if (yearMin) url += `&year_min=${{yearMin}}`;
            if (yearMax) url += `&year_max=${{yearMax}}`;
            if (format) url += `&format=${{format}}`;

            try {{
                const resp = await fetch(url);
                const data = await resp.json();

                if (data.error) {{
                    resultsDiv.innerHTML = `<div class="error">${{data.error}}</div>`;
                }} else {{
                    renderResults(data);
                }}
            }} catch(e) {{
                resultsDiv.innerHTML = `<div class="error">Search failed: ${{e.message}}</div>`;
            }} finally {{
                searchBtn.disabled = false;
                searchBtn.textContent = 'Search';
            }}
        }}

        function renderResults(data) {{
            const resultsDiv = document.getElementById('searchResults');

            if (data.found === 0) {{
                resultsDiv.innerHTML = '<div class="no-search"><p>No papers found matching your query.</p></div>';
                return;
            }}

            let html = `<div class="results-info">Found ${{data.found.toLocaleString()}} papers (${{data.search_time_ms || 0}}ms)</div>`;

            data.hits.forEach((hit, index) => {{
                const title = hit.highlights.title || hit.title;
                const fullAbstract = hit.abstract || '';
                const highlightedAbstract = hit.highlights.abstract || '';
                const truncatedAbstract = highlightedAbstract || (fullAbstract.length > 300 ? fullAbstract.substring(0, 300) + '...' : fullAbstract);
                const categories = hit.categories || [];
                const needsExpand = fullAbstract.length > 300;

                html += `
                    <div class="result-card">
                        <div class="result-title" onclick="window.open('https://arxiv.org/abs/${{hit.paper_id}}', '_blank')">${{title}}</div>
                        <div class="result-meta">
                            <span><strong>${{hit.paper_id}}</strong></span>
                            <span>${{hit.authors ? hit.authors.substring(0, 100) : ''}}</span>
                            <span>${{hit.year || ''}}</span>
                            <span>${{hit.file_type || ''}}</span>
                        </div>
                        <div class="result-abstract"
                             id="abstract-${{index}}"
                             data-full="${{fullAbstract.replace(/"/g, '&quot;')}}"
                             data-truncated="${{truncatedAbstract.replace(/"/g, '&quot;')}}"
                             data-expanded="false"
                             onclick="toggleAbstract(${{index}})">${{truncatedAbstract}}${{needsExpand ? ' <span class="abstract-hint">(click to expand)</span>' : ''}}</div>
                        <div class="result-categories">
                            ${{categories.map(c => `<span class="category-tag">${{c}}</span>`).join('')}}
                        </div>
                        <button class="download-btn" onclick="downloadPaper('${{hit.paper_id}}')">Download</button>
                    </div>
                `;
            }});

            // Pagination
            if (data.total_pages > 1) {{
                html += '<div class="pagination">';
                html += `<button ${{data.page <= 1 ? 'disabled' : ''}} onclick="doSearch(${{data.page - 1}})">Previous</button>`;
                html += `<button class="current">Page ${{data.page}} of ${{data.total_pages}}</button>`;
                html += `<button ${{data.page >= data.total_pages ? 'disabled' : ''}} onclick="doSearch(${{data.page + 1}})">Next</button>`;
                html += '</div>';
            }}

            resultsDiv.innerHTML = html;
        }}

        // Toggle abstract expand/collapse
        function toggleAbstract(index) {{
            const el = document.getElementById('abstract-' + index);
            if (!el) return;
            const isExpanded = el.dataset.expanded === 'true';
            if (isExpanded) {{
                el.innerHTML = el.dataset.truncated + ' <span class="abstract-hint">(click to expand)</span>';
                el.dataset.expanded = 'false';
                el.classList.remove('expanded');
            }} else {{
                el.innerHTML = el.dataset.full + ' <span class="abstract-hint">(click to collapse)</span>';
                el.dataset.expanded = 'true';
                el.classList.add('expanded');
            }}
        }}

        // Download functionality
        async function downloadPaper(paperId) {{
            try {{
                const response = await fetch(`/paper/${{encodeURIComponent(paperId)}}`);
                if (response.ok) {{
                    const blob = await response.blob();
                    const contentType = response.headers.get('content-type');
                    let ext = '.pdf';
                    if (contentType === 'application/gzip') ext = '.gz';
                    else if (contentType === 'application/x-tar') ext = '.tar';

                    const url = window.URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = url;
                    a.download = paperId.replace(/[^a-zA-Z0-9.-]/g, '_') + ext;
                    document.body.appendChild(a);
                    a.click();
                    window.URL.revokeObjectURL(url);
                    a.remove();
                }} else {{
                    alert('Failed to download paper');
                }}
            }} catch(e) {{
                alert('Download error: ' + e.message);
            }}
        }}

        // Download by ID form
        const downloadForm = document.getElementById('downloadForm');
        downloadForm.addEventListener('submit', async (e) => {{
            e.preventDefault();
            const paperId = document.getElementById('paper_id').value.trim();
            if (!paperId) return;

            const btn = document.getElementById('downloadBtn');
            const resultDiv = document.getElementById('downloadResult');

            btn.disabled = true;
            btn.innerHTML = '<span class="loading"></span>Fetching...';

            try {{
                const response = await fetch(`/paper/${{encodeURIComponent(paperId)}}`);
                if (response.ok) {{
                    const blob = await response.blob();
                    const contentType = response.headers.get('content-type');
                    const source = response.headers.get('x-paper-source') || 'unknown';

                    let ext = '.pdf';
                    if (contentType === 'application/gzip') ext = '.gz';
                    else if (contentType === 'application/x-tar') ext = '.tar';

                    const url = window.URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = url;
                    a.download = paperId.replace(/[^a-zA-Z0-9.-]/g, '_') + ext;
                    document.body.appendChild(a);
                    a.click();
                    window.URL.revokeObjectURL(url);
                    a.remove();

                    resultDiv.innerHTML = `<div class="success"><strong>Download started!</strong><br>Paper retrieved from: <code>${{source}}</code></div>`;
                }} else {{
                    const errorData = await response.json();
                    const detail = errorData.detail || {{}};
                    const message = detail.message || errorData.detail || 'Unknown error';
                    const tarHint = detail.tar_hint;

                    let hintHtml = '';
                    if (tarHint) {{
                        hintHtml = `
                            <div class="hint">
                                <div class="hint-title">Expected Tar File Location</div>
                                <ul>
                                    <li><strong>Directory:</strong> <code>${{tarHint.year_dir}}/</code></li>
                                    <li><strong>PDF:</strong> <code>${{tarHint.pdf_pattern}}</code></li>
                                    <li><strong>Source:</strong> <code>${{tarHint.src_pattern}}</code></li>
                                </ul>
                            </div>
                        `;
                    }}
                    resultDiv.innerHTML = `<div class="error">${{message}}</div>${{hintHtml}}`;
                }}
            }} catch(e) {{
                resultDiv.innerHTML = `<div class="error">Network error: ${{e.message}}</div>`;
            }} finally {{
                btn.disabled = false;
                btn.textContent = 'Download';
            }}
        }});
    </script>
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
        result = retriever.get_source_by_id(paper_id)

        if result["content"] is None:
            # Get detailed error information
            error_info = retriever.get_detailed_error(paper_id)
            error_type = error_info["error_type"]
            error_message = error_info["error_message"]
            tar_hint = error_info.get("tar_hint")

            if result["error"] == "version_not_found":
                error_type = "version_not_found"
                error_message = f"Requested version of paper '{paper_id}' not found."
                # Always get tar hint for version errors (base paper may exist but tar_hint would be None)
                tar_hint = get_expected_tar_pattern(paper_id)

            # Build tar hint HTML if available
            tar_hint_html = ""
            if tar_hint:
                tar_hint_html = f"""
        <div class="hint">
            <div class="hint-title">Expected Tar File Location</div>
            <p>This paper should be in one of the following arXiv bulk tar files:</p>
            <ul>
                <li><strong>Directory:</strong> <code>{tar_hint['year_dir']}/</code></li>
                <li><strong>PDF files:</strong> <code>{tar_hint['pdf_pattern']}</code></li>
                <li><strong>Source files:</strong> <code>{tar_hint['src_pattern']}</code></li>
            </ul>
            <p class="hint-note">Download bulk data from <a href="https://info.arxiv.org/help/bulk_data.html" target="_blank">arXiv Bulk Data Access</a></p>
        </div>
"""
            # Show exact archive file if known (archive_missing case)
            archive_file = error_info.get("archive_file")
            if archive_file:
                tar_hint_html = f"""
        <div class="hint">
            <div class="hint-title">Required Tar File</div>
            <p>This paper requires the following tar file:</p>
            <p><code>{archive_file}</code></p>
            <p class="hint-note">Download bulk data from <a href="https://info.arxiv.org/help/bulk_data.html" target="_blank">arXiv Bulk Data Access</a></p>
        </div>
"""

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
        .hint {{
            background-color: #e3f2fd;
            border: 1px solid #90caf9;
            border-radius: 4px;
            padding: 15px;
            margin-top: 20px;
            text-align: left;
        }}
        .hint-title {{
            font-weight: bold;
            color: #1565c0;
            margin-bottom: 10px;
        }}
        .hint ul {{
            margin: 10px 0;
            padding-left: 20px;
        }}
        .hint code {{
            background-color: #e8e8e8;
            padding: 2px 6px;
            border-radius: 3px;
            font-family: monospace;
        }}
        .hint-note {{
            font-size: 0.9em;
            color: #666;
            margin-top: 10px;
        }}
        a {{
            color: #4CAF50;
            text-decoration: none;
        }}
        a:hover {{
            text-decoration: underline;
        }}
        .hint a {{
            color: #1565c0;
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
        {tar_hint_html}
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
    content_type = result["content_type"]
    if content_type == "application/pdf":
        filename = f"{paper_id}.pdf"
    elif content_type == "application/x-tar":
        filename = f"{paper_id}.tar"
    else:
        filename = f"{paper_id}.gz"

    return Response(
        content=result["content"],
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
    - `ir_cache_configured`: Whether IR package caching is enabled
    - `arxiv_fallback_enabled`: Whether direct arXiv.org fallback is enabled
    - `search_available`: Whether Typesense search is available
    """
    import os
    return {
        "status": "healthy" if retriever else "unhealthy",
        "startup_error": startup_error,
        "upstream_configured": bool(settings.UPSTREAM_SERVER_URL),
        "upstream_enabled": settings.UPSTREAM_ENABLED,
        "cache_configured": bool(settings.CACHE_DIR_PATH),
        "ir_cache_configured": bool(settings.IR_CACHE_DIR_PATH),
        "arxiv_fallback_enabled": settings.ARXIV_FALLBACK_ENABLED,
        "search_available": search_client.is_available if search_client else False,
        "patent_configured": patent_retriever is not None,
    }


@app.get("/debug/config", tags=["Status"])
async def debug_config():
    """
    Debug endpoint showing full service configuration.

    **Response includes:**
    - All configuration paths and settings
    - Whether required files/directories exist
    - Cache statistics (if caching enabled): size, utilization, paper count
    - IR cache statistics (if IR caching enabled): size, utilization, package count
    - Search statistics (if Typesense enabled): document count
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
        "IR_CACHE_DIR_PATH": settings.IR_CACHE_DIR_PATH,
        "IR_CACHE_MAX_SIZE_GB": settings.IR_CACHE_MAX_SIZE_GB,
        "ARXIV_FALLBACK_ENABLED": settings.ARXIV_FALLBACK_ENABLED,
        "ARXIV_TIMEOUT": settings.ARXIV_TIMEOUT,
        "TYPESENSE_HOST": settings.TYPESENSE_HOST,
        "TYPESENSE_PORT": settings.TYPESENSE_PORT,
        "TYPESENSE_ENABLED": settings.TYPESENSE_ENABLED,
        "PATENT_INDEX_DB_PATH": settings.PATENT_INDEX_DB_PATH,
        "PATENT_BULK_DIR_PATH": settings.PATENT_BULK_DIR_PATH,
        "db_exists": os.path.exists(settings.INDEX_DB_PATH),
        "tar_dir_exists": os.path.exists(settings.TAR_DIR_PATH),
        "patent_index_db_exists": os.path.exists(settings.PATENT_INDEX_DB_PATH) if settings.PATENT_INDEX_DB_PATH else False,
        "patent_bulk_dir_exists": os.path.exists(settings.PATENT_BULK_DIR_PATH) if settings.PATENT_BULK_DIR_PATH else False,
        "working_directory": os.getcwd()
    }

    # Add cache stats if cache is configured
    if retriever and retriever.cache:
        config["cache_stats"] = retriever.cache.get_stats()

    # Add IR cache stats if IR cache is configured
    if ir_cache:
        config["ir_cache_stats"] = ir_cache.get_stats()

    # Add search stats if search is configured
    if search_client:
        config["search_stats"] = search_client.get_stats()

    return config


@app.get("/paper/random", tags=["Paper Retrieval"])
async def get_random_paper(
    format: Optional[PaperFormat] = Query(
        default=None,
        description="Filter by format: 'pdf' or 'source'"
    ),
    category: Optional[str] = Query(
        default=None,
        description="Filter by category (e.g., 'astro-ph', 'hep-lat', 'cond-mat'). Only applies to old-format papers currently."
    ),
    download: bool = Query(
        default=False,
        description="If true, return the paper content. If false, return metadata only."
    ),
    local_only: bool = Query(
        default=True,
        description="If true (default), only select from locally available papers. If false, select from entire database (paper fetched via upstream/arXiv)."
    )
):
    """
    Get a random paper from the database.

    **Query parameters:**
    - `format`: Filter by format - 'pdf' or 'source' (gzip/tar)
    - `category`: Filter by arXiv category (e.g., 'astro-ph', 'hep-lat'). Only works for old-format papers.
    - `download`: If true, returns the paper content. If false (default), returns metadata.
    - `local_only`: If true (default), only select from local tar files. If false, select from entire database.

    **Example requests:**
    ```
    GET /paper/random                        # Random local paper metadata
    GET /paper/random?local_only=false       # Random paper from entire database
    GET /paper/random?format=pdf             # Random PDF paper metadata
    GET /paper/random?category=astro-ph      # Random astrophysics paper (old-format)
    GET /paper/random?download=true          # Download a random paper
    GET /paper/random?local_only=false&download=true  # Download random paper (via upstream/arXiv)
    ```

    **Metadata response:**
    ```json
    {
      "paper_id": "astro-ph0412561",
      "file_type": "gzip",
      "format": "source",
      "size_bytes": 123456,
      "year": 2004,
      "locally_available": true
    }
    ```
    """
    if not retriever:
        raise HTTPException(status_code=500, detail="Service not configured")

    format_str = format.value if format else None

    # Get random paper metadata
    paper_info = retriever.get_random_paper(format=format_str, category=category, local_only=local_only)

    if paper_info is None:
        raise HTTPException(
            status_code=404,
            detail={
                "message": "No papers found matching the criteria.",
                "error": "no_matches",
                "format": format_str,
                "category": category,
            }
        )

    if not download:
        return paper_info

    # Download the paper
    result = retriever.get_source_by_id(paper_info["paper_id"], format=format_str)

    if result["content"] is None:
        raise HTTPException(
            status_code=404,
            detail=f"Paper {paper_info['paper_id']} not found."
        )

    # Build metadata headers
    headers = {
        "X-Paper-ID": result.get("paper_id", ""),
        "X-Paper-Format": result.get("format", "unknown"),
        "X-Paper-File-Type": result.get("file_type", "unknown"),
        "X-Paper-Source": result.get("source", "unknown"),
    }
    if result.get("year"):
        headers["X-Paper-Year"] = str(result["year"])

    return Response(
        content=result["content"],
        media_type=result["content_type"],
        headers=headers
    )


@app.get("/paper/categories", tags=["Paper Retrieval"])
async def get_categories():
    """
    Get list of available paper categories.

    Returns categories that can be used with `/paper/random?category=`.

    **Response fields:**
    - `legacy_categories`: From old-format paper IDs (e.g., 'astro-ph', 'hep-lat')
    - `modern_categories`: From categories column (e.g., 'astro-ph.GA', 'cs.AI')
    - `all_categories`: Combined list of all category prefixes

    **Note:** Modern categories require running the `fetch_categories.py` script
    to populate the categories column from the arXiv API.
    """
    if not retriever:
        raise HTTPException(status_code=500, detail="Service not configured")

    result = retriever.get_available_categories()
    return {
        "legacy_categories": result["legacy_categories"],
        "modern_categories": result["modern_categories"],
        "all_categories": result["all_categories"],
        "legacy_count": len(result["legacy_categories"]),
        "modern_count": len(result["modern_categories"]),
        "total_count": len(result["all_categories"]),
    }


@app.get("/search", tags=["Search"])
async def search_papers(
    q: str = Query(..., description="Search query", min_length=1),
    category: Optional[str] = Query(None, description="Filter by category (e.g., 'astro-ph', 'cs.AI')"),
    year_min: Optional[int] = Query(None, description="Minimum year", ge=1990, le=2030),
    year_max: Optional[int] = Query(None, description="Maximum year", ge=1990, le=2030),
    format: Optional[str] = Query(None, description="Filter by format: 'pdf' or 'source'"),
    page: int = Query(1, description="Page number", ge=1),
    per_page: int = Query(20, description="Results per page", ge=1, le=100),
):
    """
    Full-text search for papers by title, authors, abstract, or categories.

    **This is the primary search endpoint for finding papers.**

    **Query parameters:**
    - `q` (required): Search query - searches title, authors, abstract, categories
    - `category`: Filter by arXiv category (e.g., 'astro-ph', 'cs.AI', 'hep-th')
    - `year_min`, `year_max`: Filter by publication year range
    - `format`: Filter by file type ('pdf' or 'source')
    - `page`: Page number (default: 1)
    - `per_page`: Results per page (default: 20, max: 100)

    **Response:**
    ```json
    {
      "query": "dark matter",
      "found": 12345,
      "page": 1,
      "per_page": 20,
      "total_pages": 618,
      "hits": [
        {
          "paper_id": "2103.06497",
          "title": "Dark Matter Studies",
          "authors": "A. Einstein, N. Bohr",
          "abstract": "We present...",
          "categories": ["astro-ph.CO", "hep-ph"],
          "year": 2021,
          "file_type": "pdf",
          "highlights": {
            "title": "<mark>Dark Matter</mark> Studies"
          }
        }
      ],
      "facets": {
        "primary_category": [{"value": "astro-ph.CO", "count": 500}],
        "year": [{"value": 2024, "count": 100}]
      }
    }
    ```

    **Examples:**
    ```
    GET /search?q=machine+learning
    GET /search?q=gravitational+waves&category=gr-qc
    GET /search?q=neural+network&year_min=2020&format=pdf
    GET /search?q=cosmology&page=2&per_page=50
    ```
    """
    if not search_client or not search_client.is_available:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "search_unavailable",
                "message": "Search service is not available. Typesense may not be configured or running."
            }
        )

    result = search_client.search(
        query=q,
        category=category,
        year_min=year_min,
        year_max=year_max,
        file_type=format,
        page=page,
        per_page=per_page,
    )

    if "error" in result:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "search_error",
                "message": result["error"]
            }
        )

    return result


@app.get("/search/stats", tags=["Search"])
async def search_stats():
    """
    Get search index statistics.

    **Response:**
    ```json
    {
      "available": true,
      "collection": "papers",
      "num_documents": 1098246
    }
    ```
    """
    if not search_client:
        return {"available": False, "error": "Search not configured"}

    return search_client.get_stats()


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
        tar_hint = get_expected_tar_pattern(paper_id)
        raise HTTPException(
            status_code=404,
            detail={
                "message": f"Paper with ID '{paper_id}' not found.",
                "error": "not_found",
                "tar_hint": tar_hint,
            }
        )

    return info


class IRProfile(str, Enum):
    """IR package profile options."""
    text_only = "text-only"
    full = "full"


@app.get("/paper/{paper_id:path}/ir", tags=["Paper Retrieval"])
async def get_paper_ir(
    paper_id: str,
    profile: Optional[IRProfile] = Query(
        default=IRProfile.text_only,
        description="IR package profile: 'text-only' (default, excludes images) or 'full' (includes all files)"
    )
):
    """
    Retrieve an IR (Intermediate Representation) package for a paper.

    The IR package is a standardized format containing:
    - LaTeXML XML output (parsed LaTeX structure)
    - Source LaTeX files
    - Manifest with metadata

    This endpoint fetches the paper source and generates an IR package using
    arxiv-src-ir. The IR package is the canonical format for downstream processing
    (chunking, embedding, indexing).

    **Paper ID formats accepted:**
    - Same as `/paper/{paper_id}` endpoint

    **Profile options:**
    - `text-only` (default): Excludes binary files (images, PDFs) - smaller package
    - `full`: Includes all source files

    **Returns:**
    - IR package as `.tar.gz` with Content-Type `application/gzip`

    **Response headers:**
    - `X-Paper-ID`: Normalized paper ID
    - `X-IR-Profile`: Package profile (text-only or full)
    - `X-Cache-Status`: 'hit' if served from cache, 'miss' if freshly generated

    **Errors:**
    - `404`: Paper not found or not available as source
    - `422`: Cannot generate IR (PDF-only paper or LaTeXML failure)

    **Example:**
    ```
    GET /paper/2103.06497/ir
    GET /paper/2103.06497/ir?profile=full
    ```
    """
    from .ir import generate_ir_package

    profile_str = profile.value if profile else "text-only"

    # Check cache first (before fetching source)
    if ir_cache:
        cached_ir = ir_cache.get(paper_id, profile_str)
        if cached_ir is not None:
            # Get paper info for metadata headers (lightweight lookup)
            paper_info = retriever.get_paper_info(paper_id)
            normalized_id = paper_info.get("paper_id", paper_id) if paper_info else paper_id

            headers = {
                "X-Paper-ID": normalized_id,
                "X-IR-Profile": profile_str,
                "X-Cache-Status": "hit",
                "Content-Disposition": f'attachment; filename="{normalized_id}.ir.tar.gz"',
            }

            if paper_info and paper_info.get("year"):
                headers["X-Paper-Year"] = str(paper_info["year"])

            return Response(
                content=cached_ir,
                media_type="application/gzip",
                headers=headers
            )

    # Cache miss - fetch the paper source
    result = retriever.get_source_by_id(paper_id, format="source")

    if result["content"] is None:
        error_reason = result["error"]
        tar_hint = get_expected_tar_pattern(paper_id)

        if error_reason == "format_unavailable":
            raise HTTPException(
                status_code=422,
                detail={
                    "message": f"Paper '{paper_id}' is not available as LaTeX source. IR packages require source, not PDF.",
                    "error": "source_unavailable",
                }
            )
        else:
            raise HTTPException(
                status_code=404,
                detail={
                    "message": f"Paper with ID '{paper_id}' not found.",
                    "error": "not_found",
                    "tar_hint": tar_hint,
                }
            )

    # Generate IR package
    ir_bytes, error = generate_ir_package(
        paper_id=result.get("paper_id", paper_id),
        content=result["content"],
        profile=profile_str,
    )

    if error:
        # Don't cache failed generations
        raise HTTPException(
            status_code=422,
            detail={
                "message": f"Failed to generate IR package for '{paper_id}': {error}",
                "error": "ir_generation_failed",
            }
        )

    # Cache the successful result
    if ir_cache:
        ir_cache.put(paper_id, profile_str, ir_bytes)

    headers = {
        "X-Paper-ID": result.get("paper_id", ""),
        "X-IR-Profile": profile_str,
        "X-Cache-Status": "miss",
        "Content-Disposition": f'attachment; filename="{result.get("paper_id", paper_id)}.ir.tar.gz"',
    }

    if result.get("year"):
        headers["X-Paper-Year"] = str(result["year"])

    return Response(
        content=ir_bytes,
        media_type="application/gzip",
        headers=headers
    )


@app.post("/ir/cache/clear", tags=["Status"])
async def clear_ir_cache():
    """
    Clear the IR package cache.

    Use this endpoint after updating arxiv-src-ir to regenerate packages with the new version.

    **Returns:**
    - `cleared`: Number of cached packages removed
    - `cache_configured`: Whether IR caching is enabled

    **Note:** This operation cannot be undone. Cached packages will be regenerated
    on subsequent requests.
    """
    if not ir_cache:
        return {
            "cache_configured": False,
            "cleared": 0,
            "message": "IR cache is not configured"
        }

    count = ir_cache.clear()
    return {
        "cache_configured": True,
        "cleared": count,
        "message": f"Cleared {count} IR packages from cache"
    }


# ---------------------------------------------------------------------------
# USPTO Patent Endpoints
# ---------------------------------------------------------------------------

@app.get("/patent/{patent_id:path}/info", tags=["Patent Retrieval"])
async def get_patent_info(patent_id: str):
    """
    Get metadata about a patent without downloading its content.

    **Response fields:**
    - `patent_id`: Bare patent document number
    - `kind_code`: Kind code (B1, B2, A1, etc.)
    - `doc_type`: "grant" or "application"
    - `size_bytes`: XML size in bytes
    - `year`: Publication year
    - `locally_available`: Whether the patent ZIP is stored locally
    - `source`: Where metadata came from ("local" or "upstream")

    **Patent ID formats accepted:**
    - `US11123456B2` - With US prefix and kind code
    - `US11123456` - With US prefix only
    - `11123456` - Bare document number
    - `US20200123456A1` - Application publication number

    **Example:**
    ```
    GET /patent/US11123456B2/info
    ```
    """
    if not patent_retriever:
        raise HTTPException(
            status_code=404,
            detail={
                "message": "USPTO patent retrieval is not configured. Set PATENT_BULK_DIR_PATH.",
                "error": "not_configured",
            }
        )

    info = patent_retriever.get_patent_info(patent_id)

    if info is None:
        bare_id = normalize_patent_id(patent_id)
        raise HTTPException(
            status_code=404,
            detail={
                "message": f"Patent '{bare_id}' not found.",
                "error": "not_found",
            }
        )

    return info


@app.get("/patent/{patent_id:path}", tags=["Patent Retrieval"])
async def get_patent(patent_id: str):
    """
    Retrieve a patent by its document number. Returns raw XML.

    **This is the primary endpoint for patent retrieval.**

    **Patent ID formats accepted:**
    - `US11123456B2` - With US prefix and kind code
    - `US11123456` - With US prefix only
    - `11123456` - Bare document number
    - `US20200123456A1` - Application publication number

    **Returns:**
    - Raw XML content with `Content-Type: application/xml`

    **Response headers:**
    - `X-Patent-ID`: Bare patent document number
    - `X-Patent-Kind-Code`: Kind code (B2, A1, etc.) if known
    - `X-Patent-Doc-Type`: "grant" or "application"
    - `X-Patent-Source`: Where patent was retrieved from (local, upstream)

    **Examples:**
    ```
    GET /patent/11123456
    GET /patent/US11123456B2
    GET /patent/US20200123456A1
    ```
    """
    if not patent_retriever:
        raise HTTPException(
            status_code=404,
            detail={
                "message": "USPTO patent retrieval is not configured. Set PATENT_BULK_DIR_PATH.",
                "error": "not_configured",
            }
        )

    result = patent_retriever.get_patent_by_id(patent_id)

    if result["content"] is None:
        bare_id = normalize_patent_id(patent_id)
        raise HTTPException(
            status_code=404,
            detail={
                "message": f"Patent '{bare_id}' not found.",
                "error": "not_found",
            }
        )

    headers = {
        "X-Patent-ID": result.get("patent_id", ""),
        "X-Patent-Source": result.get("source", "unknown"),
    }
    if result.get("kind_code"):
        headers["X-Patent-Kind-Code"] = result["kind_code"]
    if result.get("doc_type"):
        headers["X-Patent-Doc-Type"] = result["doc_type"]

    return Response(
        content=result["content"],
        media_type="application/xml",
        headers=headers,
    )


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

    **Response headers include metadata:**
    - `X-Paper-ID`: Normalized paper ID
    - `X-Paper-Format`: Format category (pdf, source, unknown)
    - `X-Paper-File-Type`: Specific file type (pdf, gzip, tar, unknown)
    - `X-Paper-Year`: Publication year (if known)
    - `X-Paper-Version`: Requested version (if specified)
    - `X-Paper-Source`: Where paper was retrieved from (local, cache, upstream)

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
    result = retriever.get_source_by_id(paper_id, format=format_str)

    if result["content"] is None:
        error_reason = result["error"]
        tar_hint = get_expected_tar_pattern(paper_id)

        if error_reason == "version_not_found":
            raise HTTPException(
                status_code=404,
                detail={
                    "message": f"Requested version of paper '{paper_id}' not found.",
                    "error": "version_not_found",
                    "tar_hint": tar_hint,
                }
            )
        elif error_reason == "format_unavailable":
            raise HTTPException(
                status_code=404,
                detail={
                    "message": f"Paper '{paper_id}' is not available in '{format_str}' format.",
                    "error": "format_unavailable",
                    "tar_hint": None,
                }
            )
        else:
            raise HTTPException(
                status_code=404,
                detail={
                    "message": f"Paper with ID '{paper_id}' not found.",
                    "error": "not_found",
                    "tar_hint": tar_hint,
                }
            )

    # Build metadata headers
    headers = {
        "X-Paper-ID": result.get("paper_id", ""),
        "X-Paper-Format": result.get("format", "unknown"),
        "X-Paper-File-Type": result.get("file_type", "unknown"),
        "X-Paper-Source": result.get("source", "unknown"),
    }

    # Only add optional headers if values are present
    if result.get("year"):
        headers["X-Paper-Year"] = str(result["year"])
    if result.get("version"):
        headers["X-Paper-Version"] = str(result["version"])

    return Response(
        content=result["content"],
        media_type=result["content_type"],
        headers=headers
    )