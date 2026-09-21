FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app

COPY src/ /app/src/

USER 65532:65532

ENTRYPOINT ["python3", "-m", "codex_notify_gateway"]
