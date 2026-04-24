FROM ghcr.io/astral-sh/uv:python3.13-bookworm AS base

WORKDIR /app/

COPY requirements.txt .
RUN uv pip install -r requirements.txt --system

COPY . .

ENV PYTHONPATH=/app

# Regenerate reference.md against the installed policyengine_uk_compiled version
# so every deployed image ships a fresh API reference (not the checked-in copy).
RUN python scripts/build_reference.py
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080", "--reload"]
