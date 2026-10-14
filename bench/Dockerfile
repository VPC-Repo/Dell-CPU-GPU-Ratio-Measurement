# syntax=docker/dockerfile:1
# The measurement image: agent runtime, capacity controller, sandboxed jobs,
# model stand-in, reranker, samplers, and every asset a run needs, prebuilt,
# so a server never downloads anything at run time.
#
# Toolchain matches the PowerEdge R770 results of record: Ubuntu 26.04
# (GCC 15.2, Tesseract 5.5), Python 3.12.14, the same Python packages.
# The base is pinned by digest so a build on any server gets the same
# compilers and OCR engine as the record.
FROM ubuntu:26.04@sha256:f144425ff09be612d6d9ad965196e9cdc23dae1f42110a8a11a3e9a8198759f7

ARG PYTHON_VERSION=3.12.14
ARG RERANK_REPO=cross-encoder/ms-marco-MiniLM-L6-v2
ARG RERANK_REVISION=233902d25c440f23af6f7d6e94d2946bac0bee0a

ENV DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8 LC_ALL=C.UTF-8 \
    UV_PYTHON_INSTALL_DIR=/opt/python UV_LINK_MODE=copy

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl gcc make libc6-dev tesseract-ocr tesseract-ocr-eng nodejs \
        sysstat util-linux procps iproute2 sudo dmidecode tini tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.12.18 /uv /usr/local/bin/uv
COPY --from=docker:28.5.1-cli /usr/local/bin/docker /usr/local/bin/docker

# Non-root user that runs the fleet; sandboxed jobs drop to it inside their
# own network namespace (sudo unshare -n), as on the reference server.
RUN useradd -m -u 61000 -s /bin/bash bench \
    && echo 'bench ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/bench && chmod 0440 /etc/sudoers.d/bench

WORKDIR /opt/bench
COPY bench/requirements.txt .
RUN uv python install ${PYTHON_VERSION} \
    && uv venv -p ${PYTHON_VERSION} .venv \
    && uv pip install -p .venv/bin/python --no-cache -r requirements.txt \
    && ln -s /opt/bench/.venv/bin/python /usr/local/bin/python3

COPY bench/backend backend
COPY bench/config config
COPY bench/scripts scripts
COPY bench/runner runner
COPY bench/ui ui
COPY ui/replay ui/replay
COPY bench/data/capacity/build /opt/assets/build

# Prebuilt assets, outside the run-data volume.
ENV CAPACITY_BUILD_SRC=/opt/assets/build \
    CAPACITY_HTML_DOCS=/opt/assets/html \
    CAPACITY_INGEST_DOCS=/opt/assets/ingest \
    CAPACITY_CORPUS_DIR=/opt/assets/retrieval \
    XEON_MODEL_ROOT=/opt/assets/models \
    PYTHONPATH=/opt/bench
RUN .venv/bin/python -c "from backend.capacity import sandbox; print(sandbox._build_tree())" \
    && .venv/bin/python -c "from backend.capacity import retrieval; print(retrieval.ensure_corpus())" \
    && .venv/bin/python scripts/make_ingest_docs.py --docs 40 --pages 20 --out /opt/assets/ingest \
    && .venv/bin/python scripts/make_html_docs.py --pages 300 --out /opt/assets/html
# The INT8 reranker: the published ONNX graph at a pinned revision, quantized
# here exactly as scripts/provision_retrieval.sh does on a host.
RUN mkdir -p /opt/assets/models/ms-marco-int8/onnx && .venv/bin/python - <<EOF
import shutil
from huggingface_hub import hf_hub_download
from onnxruntime.quantization import quantize_dynamic, QuantType
out = "/opt/assets/models/ms-marco-int8"
for f in ("config.json", "tokenizer.json", "tokenizer_config.json"):
    shutil.copy(hf_hub_download("${RERANK_REPO}", f, revision="${RERANK_REVISION}"), f"{out}/{f}")
shutil.copy(hf_hub_download("${RERANK_REPO}", "onnx/model.onnx", revision="${RERANK_REVISION}"), f"{out}/onnx/model_fp32.onnx")
quantize_dynamic(f"{out}/onnx/model_fp32.onnx", f"{out}/onnx/model.onnx", weight_type=QuantType.QInt8)
EOF
RUN rm -rf /root/.cache && chown -R bench:bench /opt/assets /opt/bench

COPY bench/entrypoint.sh /usr/local/bin/entrypoint.sh
ENTRYPOINT ["/usr/bin/tini", "-s", "--", "/usr/local/bin/entrypoint.sh"]
CMD ["serve"]
