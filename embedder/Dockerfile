# Query and document embedder: the Text Embeddings Inference CPU image used for
# the PowerEdge R770 results of record, with all-MiniLM-L6-v2 built in so a
# server never downloads the model. The ONNX graph is included so TEI runs its
# ONNX Runtime backend (without it TEI falls back to Candle, several times slower).
FROM python:3.12-slim AS model
ARG EMBED_REPO=sentence-transformers/all-MiniLM-L6-v2
ARG EMBED_REVISION=1110a243fdf4706b3f48f1d95db1a4f5529b4d41
RUN pip install --no-cache-dir huggingface_hub==1.12.0 \
    && python -c "from huggingface_hub import snapshot_download; snapshot_download('${EMBED_REPO}', revision='${EMBED_REVISION}', local_dir='/model', allow_patterns=['*.json', 'model.safetensors', 'onnx/model.onnx', '1_Pooling/*', 'vocab.txt'])"

FROM ghcr.io/huggingface/text-embeddings-inference@sha256:cc1f06b5b2e38d979aca1cc054675426bf4d94247f6d22e399d73e09131dfe79
COPY --from=model /model /model
ENV HF_HUB_OFFLINE=1
CMD ["--model-id", "/model"]
