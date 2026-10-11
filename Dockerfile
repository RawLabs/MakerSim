# Local stdio MCP server, including the bundled example and material data.
FROM python:3.12-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.12.7 /uv /usr/local/bin/uv

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MPLBACKEND=Agg \
    MPLCONFIGDIR=/tmp/matplotlib \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1

COPY pyproject.toml uv.lock LICENSE ./
RUN uv sync --locked --no-dev --no-install-project
COPY backend/ ./backend/
RUN uv sync --locked --no-dev --no-editable

RUN useradd --uid 10001 --create-home makersim \
    && mkdir -p /data/sessions \
    && chown -R makersim:makersim /data
ENV MAKERSIM_SESSION_DIR=/data/sessions
USER makersim

# Keep stdin open when launching the image; MCP messages use stdin/stdout.
ENTRYPOINT ["/app/.venv/bin/makersim-mcp"]
