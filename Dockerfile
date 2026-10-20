FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install .

# Non-root. Mount the folder you want read read-only:  docker run -i --rm -v "$PWD:/data:ro" filelens --root /data
RUN useradd --create-home app
USER app

EXPOSE 8000
ENTRYPOINT ["filelens"]
