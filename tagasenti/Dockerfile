# tagasenti - TagaSenti INT8 ONNX inference service
#
# Build:  docker compose build tagasenti   (from ~/n8n)
# Result: ~2.2GB image (562MB model + ~1.4GB python/onnxruntime/transformers)
#
# WHY THESE PINS
#   protobuf<6      - Unpinned protobuf pulls 7.x, which breaks onnxruntime
#                     and transformers imports. The build machine's own working
#                     Colab script flags this exact trap; do not remove the pin.
#   slim not full   - we need no compilers at runtime. The slim base roughly
#                     halves the image versus the full python image.
#
# WHY NO --no-cache-dir IS ABSENT HERE
#   It is present. See the pip line. Layer caching matters here because the
#   562MB model is copied in a LATER layer, so a pip-only change does not
#   invalidate it.

FROM python:3.12-slim
WORKDIR /app

# curl is needed for the compose healthcheck below.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Dependency layer first so it caches across model swaps.
RUN pip install --no-cache-dir \
        "protobuf>=5.26,<6" \
        fastapi \
        uvicorn \
        onnxruntime \
        transformers \
        numpy

# Model + tokenizer + code. TagaSenti is Apache-2.0; see
# agent_journal/ for the required attribution.
COPY main.py tagasenti_int8.onnx ./
COPY tokenizer/ ./tokenizer/

# Run unprivileged. The python:slim image ships a `nobody` user already.
USER nobody

ENV MODEL_PATH=/app/tagasenti_int8.onnx \
    TOKENIZER_DIR=/app/tokenizer \
    BATCH_SIZE=8 \
    MAX_LENGTH=128 \
    MAX_BATCH_REVIEWS=2000 \
    ORT_INTRA_OP_THREADS=4 \
    PYTHONUNBUFFERED=1

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

# --workers 1 is DELIBERATE and important.
#   The ONNX session holds ~600MB of int8 weights. With more than one uvicorn
#   worker, each worker loads its own copy, so 4 workers would try to hold
#   2.4GB and thrash. Scale with a second *container* if you ever need more
#   throughput, not with more workers in one container.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
