# filingLens API + web UI. Built by docker compose (service "app"); see docker-compose.yml.

# ---- web UI ---------------------------------------------------------------------------------
FROM node:24.22.3-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# ---- API ------------------------------------------------------------------------------------
FROM python:3.14.9-slim-trixie
ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    FILINGLENS_WEB=/app/web/dist \
    PATH=/app/.venv/bin:$PATH
RUN pip install --no-cache-dir uv==0.13.8
WORKDIR /app
# dependencies first (cached layer): api + llm + embed (torch with CUDA for BGE-M3 on the GPU)
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project --extra api --extra llm --extra embed
COPY src/ src/
RUN uv sync --frozen --extra api --extra llm --extra embed
COPY --from=web /web/dist /app/web/dist
EXPOSE 8765
CMD ["filinglens", "serve", "--host", "127.0.0.1", "--port", "8765"]
