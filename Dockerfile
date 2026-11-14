FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/backend

WORKDIR /app

# dependencies first (cached layer unless requirements change)
COPY backend/requirements.txt backend/requirements.txt
RUN pip install -r backend/requirements.txt

# non-root user with a fixed UID so the host ./data bind mount can be chowned to it
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin stoprop \
 && mkdir -p /app/data && chown stoprop:stoprop /app/data

# app code stays root-owned (read-only for the app user)
COPY . .

# baked in last so code layers stay cached; shown by /api/health
ARG GIT_COMMIT=unknown
ENV GIT_COMMIT=$GIT_COMMIT

USER stoprop
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
