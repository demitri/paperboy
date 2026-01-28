# Build from repo root: docker build -f docker/Dockerfile -t paperboy .
FROM python:3.9-slim

WORKDIR /app

# Install LaTeXML and its dependencies
# LaTeXML requires Perl and various Perl modules
RUN apt-get update && apt-get install -y --no-install-recommends \
    latexml \
    && rm -rf /var/lib/apt/lists/*

# Verify LaTeXML installation
RUN latexml --VERSION

# Set LaTeXML binary path (required by arxiv-src-ir)
ENV LATEXML_BIN=/usr/bin/latexml

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install arxiv-src-ir from local source
# The build script copies arxiv-src-ir/python to ./arxiv-src-ir before building
COPY arxiv-src-ir /tmp/arxiv-src-ir
RUN pip install --no-cache-dir /tmp/arxiv-src-ir && rm -rf /tmp/arxiv-src-ir

# Copy application code
COPY source/paperboy/ ./paperboy/

EXPOSE 8000

CMD ["uvicorn", "paperboy.main:app", "--host", "0.0.0.0", "--port", "8000"]
