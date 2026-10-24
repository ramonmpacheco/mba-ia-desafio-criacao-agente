FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim
WORKDIR /app
ENV UV_LINK_MODE=copy
COPY pyproject.toml uv.lock .python-version ./
COPY aurora ./aurora
RUN uv sync --locked --no-dev
COPY dados ./dados
EXPOSE 8000
CMD ["uv", "run", "--no-sync", "aurora", "serve", "--host", "0.0.0.0"]
