FROM python:3.12-slim-bookworm 
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

COPY app/ .
COPY pyproject.toml .
COPY uv.lock .

RUN /bin/uv run manage.py load database
RUN /bin/uv run manage.py load cards

EXPOSE 5001
CMD ["/bin/uv", "run", "main.py"]

