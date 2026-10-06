FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir uv
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY alembic.ini ./
COPY migrations ./migrations
COPY src ./src
RUN uv sync --locked --no-dev
ENV PATH="/app/.venv/bin:${PATH}"
ENV PORT=8080
RUN groupadd --system --gid 10001 travel \
    && useradd --system --uid 10001 --gid 10001 --create-home travel \
    && chown -R travel:travel /app
USER 10001:10001
CMD ["sh", "-c", "exec uvicorn personal_travel.main:app --host 0.0.0.0 --port \"$PORT\" --no-access-log"]
