FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PYTHONOPTIMIZE=2 \
    # Performance tuning for 5 concurrent calls
    MALLOC_ARENA_MAX=2 \
    PYTHONHASHSEED=0

WORKDIR /app

# -------- BUILDER (Cython compile + build wheels) --------
FROM base AS builder

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
COPY requirements-build.txt .

# Build wheels for all dependencies (faster, cacheable, portable)
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements-build.txt
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt

# Install build dependencies from wheels
RUN pip install --no-cache-dir --no-index --find-links /wheels -r requirements-build.txt

COPY . .

# Compile agent/*.py files to .so
RUN python setup.py build_ext --inplace

# Copy .so files from /app to /app/agent
RUN cp /app/*.so /app/agent/ 2>/dev/null || true

# Strip debug symbols from compiled files
RUN find /app/agent -name "*.so" -exec strip {} \;

# Create __init__.py to make agent a package
RUN touch /app/agent/__init__.py

# Create wrapper script to handle download-files and agent startup
RUN printf '#!/usr/bin/env python\nimport sys\nimport os\n\nos.chdir("/app")\nsys.path.insert(0, "/app")\n\nif __name__ == "__main__":\n   # Default: run the agent\n    from livekit import agents\n    from agent.web_rtc_server import server\n    print("🚀 Starting agent...")\n    agents.cli.run_app(server)\n' > /app/start_agent.py && \
    chmod +x /app/start_agent.py

# Delete ONLY the .py files that have been compiled to .so (to protect IP)
# But keep them if .so compilation failed
RUN for file in \
    /app/agent/db_storage.py \
    /app/agent/metrics.py \
    /app/agent/survey_agent.py \
    /app/agent/web_rtc_server.py \
    /app/api/customer_api.py \
    /app/api/smartflo_client.py \
    /app/db/database.py \
    /app/db/models.py \
    /app/db/utils.py \
    /app/smart-flo/smartflow_bridge.py; do \
        base=$(basename "$file" .py); \
        dir=$(dirname "$file"); \
        if [ -f "$dir/$base.so" ] || [ -f "$dir/${base}.cpython-*.so" ]; then \
            echo "Removing $file (compiled version exists)"; \
            rm -f "$file"; \
        else \
            echo "Keeping $file (no compiled version found)"; \
        fi; \
    done

# Clean up build artifacts
RUN find /app -name "*.c" -type f -delete

# -------- RUNTIME (Production - Clean) --------
FROM python:3.13-slim AS runtime

ENV PYTHONOPTIMIZE=2 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    # Performance tuning for concurrent calls
    MALLOC_ARENA_MAX=2 \
    PYTHONHASHSEED=0 \
    # Async I/O optimizations
    PYTHONASYNCIODEBUG=0

WORKDIR /app

# Install runtime dependencies for LiveKit
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libglib2.0-0 \
        libgobject-2.0-0 \
        libgstreamer1.0-0 \
        libgstreamer-plugins-base1.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Copy pre-built wheels from builder
COPY --from=builder /wheels /wheels
COPY --from=builder /app/requirements.txt /app/requirements.txt

# Install from wheels (fast, no compilation)
RUN pip install --no-cache-dir --no-index --find-links /wheels -r /app/requirements.txt \
    && rm -rf /wheels /app/requirements.txt


# Copy ONLY what's needed for runtime
COPY --from=builder /app/agent/ /app/agent/
COPY --from=builder /app/api/ /app/api/
COPY --from=builder /app/db/ /app/db/
COPY --from=builder /app/smart-flo/ /app/smart-flo/
COPY --from=builder /app/start_agent.py /app/start_agent.py

# Download model files during build to include them in the image
RUN python3 start_agent.py download-files

EXPOSE 8000

# Run the LiveKit agent by default
#ENTRYPOINT ["python", "/app/start_agent.py"]
CMD ["bash"]

