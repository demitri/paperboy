from fastapi import FastAPI, HTTPException, Response, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .config import Settings
from .retriever import PaperRetriever, RetrievalError

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
    description="A microservice for delivering academic papers from arXiv bulk archives",
    version="1.0.0"
)

templates = Jinja2Templates(directory="templates")


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
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
                       placeholder="e.g., hep-lat9107001, astro-ph9205002, 1234.5678" 
                       required>
            </div>
            <button type="submit">Download Paper</button>
        </form>
        
        <div class="examples">
            <h3>Example Paper IDs:</h3>
            <ul>
                <li><code>hep-lat9107001</code> - Old format with subject class</li>
                <li><code>astro-ph9205002</code> - Astrophysics paper from 1992</li>
                <li><code>1234.5678</code> - New format (year.number)</li>
                <li><code>2103.06497</code> - Paper from March 2021</li>
            </ul>
        </div>
    </div>
</body>
</html>
    """)


@app.post("/download")
async def download_paper(paper_id: str = Form(...)):
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
        source_code = retriever.get_source_by_id(paper_id)
        
        if source_code is None:
            # Get detailed error information
            error_type, error_message = retriever.get_detailed_error(paper_id)
            
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
    
    # Determine the appropriate filename and media type
    if paper_id.endswith('.pdf') or b'%PDF' in source_code[:100]:
        filename = f"{paper_id}.pdf"
        media_type = "application/pdf"
    else:
        filename = f"{paper_id}.gz"
        media_type = "application/gzip"
    
    return Response(
        content=source_code,
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@app.get("/debug/config")
async def debug_config():
    """Debug endpoint to check configuration"""
    import os
    return {
        "INDEX_DB_PATH": settings.INDEX_DB_PATH,
        "TAR_DIR_PATH": settings.TAR_DIR_PATH,
        "db_exists": os.path.exists(settings.INDEX_DB_PATH),
        "tar_dir_exists": os.path.exists(settings.TAR_DIR_PATH),
        "working_directory": os.getcwd()
    }


@app.get("/paper/{paper_id}")
async def get_paper(paper_id: str):
    source_code = retriever.get_source_by_id(paper_id)
    
    if source_code is None:
        raise HTTPException(
            status_code=404,
            detail=f"Paper with ID '{paper_id}' not found."
        )
    
    return Response(content=source_code, media_type="text/plain")