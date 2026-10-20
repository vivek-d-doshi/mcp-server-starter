FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install .

# Run as a non-root user; notes persist only if /data is mounted and NOTES_FILE points into it.
RUN useradd --create-home app && mkdir /data && chown app /data
USER app
ENV NOTES_FILE=/data/notes.json

EXPOSE 8000
# Default is stdio (what MCP clients launch). For HTTP: docker run -p 8000:8000 notes-mcp --transport streamable-http --host 0.0.0.0
ENTRYPOINT ["python", "-m", "notes_mcp"]
