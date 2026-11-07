# The API image. Build context is backend/ (see docker-compose.yml).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# A non-root user, and two directories it owns. Named volumes mounted on these inherit that
# ownership, so the process never needs to write anywhere else (the compose file makes the
# rest of the filesystem read-only).
RUN useradd --system --uid 10001 --no-create-home modelhub \
    && mkdir -p /data/uploads /data/secrets \
    && chown -R modelhub /data
ENV UPLOAD_DIR=/data/uploads \
    SECRETS_DIR=/data/secrets
USER modelhub

EXPOSE 8001
# One worker on purpose: login throttling and running model containers live in this process.
# Migrations run first, so a fresh database is ready before the first request.
CMD ["sh", "-c", "python -m alembic upgrade head && exec uvicorn main:app --host 0.0.0.0 --port 8001"]
