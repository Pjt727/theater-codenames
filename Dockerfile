FROM python:3.12-slim-bookworm 
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

COPY app/ .
COPY pyproject.toml .
COPY uv.lock .

ENV DB_PATH=/data/cards.db
RUN mkdir -p /data \
    && /bin/uv run manage.py load database \
    && /bin/uv run manage.py load cards

EXPOSE 5001
CMD ["/bin/uv", "run", "main.py"]
