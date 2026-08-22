# AgentOS runtime image.
# Slim Python 3.11 + the core dependency set; extras install per-deployment.
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Core deps first for layer caching.
COPY pyproject.toml README.md ./
COPY agentos ./agentos
RUN pip install --no-cache-dir .[database,queue]

COPY . .

EXPOSE 8080

# API is the default process; compose overrides per service (worker).
CMD ["uvicorn", "agentos.interfaces.api:get_app", "--factory", "--host", "0.0.0.0", "--port", "8080"]
