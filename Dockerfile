FROM nvidia/cuda:12.6.3-cudnn-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/data/hf_cache

# Install Python 3.11
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3.11 python3.11-dev python3-pip git curl && \
    ln -sf /usr/bin/python3.11 /usr/bin/python3 && \
    ln -sf /usr/bin/python3 /usr/bin/python && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# PyTorch with CUDA 12.6 wheels (must come before other deps)
RUN pip install --no-cache-dir \
    torch \
    --index-url https://download.pytorch.org/whl/cu126

# Remaining dependencies (no vllm — not needed for server.py)
COPY requirements-docker.txt .
RUN pip install --no-cache-dir -r requirements-docker.txt

COPY server.py .

EXPOSE 6000

# Model weights are downloaded here at first startup and reused via the mounted volume
VOLUME ["/data/hf_cache"]

CMD ["python", "server.py"]
