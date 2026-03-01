FROM node:18-alpine AS frontend-builder

WORKDIR /app/frontend

# Copy package files
COPY frontend/package.json frontend/yarn.lock* frontend/package-lock.json* ./

# Install dependencies (use npm install for flexibility)
RUN npm install --prefer-offline --no-audit --legacy-peer-deps

# Copy frontend source
COPY frontend/src ./src
COPY frontend/public ./public

# Build React app
RUN npm run build

# -------- STAGE 2: Python Builder (Cython + Dependencies) --------
FROM python:3.13-slim AS py-builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PYTHONOPTIMIZE=2

WORKDIR /app

# Install build dependencies
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential gcc git \
    && rm -rf /var/lib/apt/lists/*

# Copy Python requirements
COPY requirements.txt requirements-build.txt ./

# Build wheels for all dependencies
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements-build.txt
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt

# Install build dependencies from wheels
RUN pip install --no-cache-dir --no-index --find-links /wheels -r requirements-build.txt

# Copy full project for compilation
COPY . .

# Compile Python files to .so with Cython
RUN python setup.py build_ext --inplace

# Copy compiled .so files to proper locations
RUN cp /app/*.so /app/agent/ 2>/dev/null || true
RUN cp /app/*.so /app/api/ 2>/dev/null || true
RUN cp /app/*.so /app/db/ 2>/dev/null || true

# Strip debug symbols from compiled files
RUN find /app -name "*.so" -exec strip {} \;

# Create __init__.py files for packages
RUN touch /app/api/__init__.py \
    /app/db/__init__.py \
    /app/agent/__init__.py

# Delete compiled .py files (keep source for non-compiled files)
RUN rm -f /app/agent/db_storage.py \
    /app/agent/metrics.py \
    /app/agent/survey_agent.py \
    /app/agent/web_rtc_server.py \
    /app/api/customer_api.py \
    /app/api/smartflo_client.py \
    /app/db/database.py \
    /app/db/models.py \
    /app/db/utils.py \
    /app/smart-flo/smartflow_bridge.py

# Clean up build artifacts
RUN find /app -name "*.c" -type f -delete

# -------- STAGE 3: Runtime (Production) --------
FROM python:3.13-slim AS runtime

ENV PYTHONOPTIMIZE=2 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    # Performance tuning for 5 concurrent calls
    MALLOC_ARENA_MAX=2 \
    PYTHONHASHSEED=0 \
    PYTHONASYNCIODEBUG=0

WORKDIR /app

# Install runtime dependencies for LiveKit and performance
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        postgresql-client \
        libglib2.0-0 \
        libgobject-2.0-0 \
        libgstreamer1.0-0 \
        libgstreamer-plugins-base1.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Copy wheels and install from builder
COPY --from=py-builder /wheels /wheels
COPY --from=py-builder /app/requirements.txt ./requirements.txt

# Install all dependencies from pre-built wheels (fast, no compilation needed)
RUN pip install --no-cache-dir --no-index --find-links /wheels -r requirements.txt \
    && rm -rf /wheels requirements.txt

# Verify critical packages
RUN python -c "import fastapi, uvicorn, sqlalchemy; print('✅ All required packages installed')" || exit 1

# Copy compiled Python modules from builder
COPY --from=py-builder /app/agent/ ./agent/
COPY --from=py-builder /app/api/ ./api/
COPY --from=py-builder /app/db/ ./db/
COPY --from=py-builder /app/smart-flo/ ./smart-flo/

# Copy non-compiled source files (needed at runtime)
COPY api/main.py api/auto_dialer.py ./api/
COPY db/init_db.py ./db/
COPY smart-flo/smartflow_bridge.py ./smart-flo/

# Copy built React frontend from frontend-builder
COPY --from=frontend-builder /app/frontend/build ./frontend/build

# Create required directories
RUN mkdir -p ./logs ./uploads

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

EXPOSE 8000 5173

# Run FastAPI server with Uvicorn (serves both API and frontend)
# Performance optimized for 5 concurrent calls
ENTRYPOINT ["python", "-m", "uvicorn"]
CMD ["api.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "1", \
     "--log-level", "info", \
     "--no-access-log", \
     "--limit-concurrency", "50", \
     "--backlog", "100"]

