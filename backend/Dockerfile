FROM ghcr.io/astral-sh/uv:python3.13-bookworm AS base

WORKDIR /app/

COPY requirements.txt .
RUN uv pip install -r requirements.txt --system

COPY . .

ENV PYTHONPATH=/app
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080", "--reload"]
