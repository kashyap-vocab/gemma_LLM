FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
COPY requirements-build.txt .

RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir -r requirements-build.txt

COPY . .

# Compile selected Python modules to .so
RUN python setup.py build_ext --inplace

# Strip symbols (harder reverse engineering + smaller)
RUN find /app -name "*.so" -exec strip --strip-unneeded {} \; || true

# Remove most source files, keep only required launchers
RUN find /app -name "*.py" -type f \
    ! -path "/app/api/main.py" \
    ! -path "/app/agent/web_rtc_server.py" \
    ! -path "/app/db/init_db.py" \
    ! -path "/app/setup.py" \
    -delete

# -------- RUNTIME --------
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PYTHONPATH=/app:/app/agent:/app/smart-flo

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --from=builder /app /app

EXPOSE 8000

CMD ["python", "-m", "api.main"]
