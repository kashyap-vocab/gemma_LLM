FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PYTHONOPTIMIZE=2

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

# -------- RUNTIME (Production - Clean) --------
FROM python:3.13-slim AS runtime

ENV PYTHONOPTIMIZE=2 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

# Copy pre-built wheels from builder
COPY --from=builder /wheels /wheels
COPY --from=builder /app/requirements.txt /app/requirements.txt

# Install from wheels (fast, no compilation)
RUN pip install --no-cache-dir --no-index --find-links /wheels -r /app/requirements.txt \
    && rm -rf /wheels /app/requirements.txt


# Copy ONLY what's needed for runtime
COPY --from=builder /app/agent/ /app/agent/
COPY --from=builder /app/start_agent.py /app/start_agent.py

# Download model files during build to include them in the image
RUN python3 start_agent.py download-files

EXPOSE 8000

# Run the LiveKit agent by default
#ENTRYPOINT ["python", "/app/start_agent.py"]
CMD ["bash"]

