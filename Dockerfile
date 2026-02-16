FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1

WORKDIR /app

# -------- BUILDER --------
FROM base AS builder

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
COPY requirements-build.txt .

RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir -r requirements-build.txt

COPY . .

# Compile all python files to .so
RUN python setup.py build_ext --inplace

# Strip symbols (harder reverse engineering + smaller)
RUN find /app -name "*.so" -exec strip {} \;

# Remove original source code (CRITICAL)
RUN find /app -name "*.py" -type f -delete

# -------- RUNTIME --------
FROM python:3.11-slim AS runtime

WORKDIR /app

COPY --from=builder /app /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

EXPOSE 8319

CMD ["python", "smartflow_bridge"]
