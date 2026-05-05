FROM ghcr.io/astral-sh/uv:python3.13-bookworm AS base

WORKDIR /app/

COPY requirements.txt .
RUN uv pip install -r requirements.txt --system

COPY . .

ENV PYTHONPATH=/app

# reference.md is generated, not committed — build it against the installed
# policyengine_uk_compiled version so every image ships a fresh API reference.
# modal_app.py runs the equivalent step for the Modal deploy path.
RUN python scripts/build_reference.py
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080", "--reload"]
