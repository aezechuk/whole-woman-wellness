FROM python:3.12-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 app

WORKDIR /app
COPY pyproject.toml README.md ./
COPY wellness ./wellness
COPY migrations ./migrations
# Install must succeed; only the cleanup step may fail quietly.
RUN pip install --no-compile . \
    && (pip uninstall -y pip setuptools wheel || true) \
    && flask --version && gunicorn --version

USER app
ENV APP_ENV=prod FLASK_APP="wellness.app:create_app()"
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz',timeout=4).status==200 else 1)"
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "--threads", "4", "--access-logfile", "-", \
     "--access-logformat", "%(t)s \"%(m)s %(U)s\" %(s)s %(L)ss", "wellness.app:create_app()"]
