FROM ghcr.io/astral-sh/uv:0.12.5 AS uv
FROM python:3.12.13-slim
COPY --from=uv /uv /usr/local/bin/uv
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 libstdc++6 ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 voiceguard && useradd --uid 10001 --gid 10001 --no-create-home voiceguard
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY *.py ./
COPY LICENSE MODELS.md THIRD_PARTY_NOTICES.md ./
RUN .venv/bin/python download_models.py && .venv/bin/python model_smoke.py \
    && rm -f test_*.py bootstrap.py
ENV PATH="/app/.venv/bin:$PATH" VOICEGUARD_DB=/data/voiceguard.sqlite VOICEGUARD_ENABLED=false
EXPOSE 8080
ENTRYPOINT ["/app/.venv/bin/python", "entrypoint.py"]
