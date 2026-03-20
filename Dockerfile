FROM python:3.12-slim AS base

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir -e .

# Copy source
COPY src/ src/
COPY tests/ tests/

# Create data directory
RUN mkdir -p data/raw data/digests

# Health check
HEALTHCHECK --interval=60s --timeout=5s \
    CMD python -c "from src.db import Database; db = Database(); print(f'vessels: {db.vessel_count()}')" || exit 1

ENTRYPOINT ["python", "-m", "src.cli"]
CMD ["--help"]