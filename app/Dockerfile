# Image used by the demos (`app`) and by the embedding step (`embed`).
# Versions come from .env via docker-compose build args.
ARG PYTHON_VERSION=3.12
FROM python:${PYTHON_VERSION}-slim

ARG COUCHBASE_SDK_VERSION
ARG FASTEMBED_VERSION
ARG EMBEDDING_MODEL

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    FASTEMBED_CACHE_PATH=/opt/models

WORKDIR /app

# Python libraries:
#   couchbase  - the Couchbase Python SDK 4.x
#   fastembed  - small, fast, local embedding models (ONNX; no API key needed)
#   rich       - readable, colourful console output
RUN pip install \
      "couchbase==${COUCHBASE_SDK_VERSION}" \
      "fastembed==${FASTEMBED_VERSION}" \
      "numpy==2.5.4" \
      "rich==14.2.0" \
      "requests==2.32.5"

# Download the embedding model NOW, while building the image, so the demos
# never need internet access (or wait for a download) at run time.
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('${EMBEDDING_MODEL}')"

COPY . /app

# Running the container with no arguments runs all three demos in order.
CMD ["python", "run_all.py"]
