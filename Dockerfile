FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

ARG APP_USER=lastping
ARG APP_GROUP=lastping
ARG APP_HOME=/app

WORKDIR ${APP_HOME}

# system deps for common Python packages
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential gcc curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd -r ${APP_GROUP} && useradd -r -g ${APP_GROUP} -d ${APP_HOME} -s /sbin/nologin ${APP_USER}

COPY requirements.txt ${APP_HOME}/requirements.txt
RUN pip install --upgrade pip && pip install --no-cache-dir -r ${APP_HOME}/requirements.txt

# Copy app and set ownership
COPY . ${APP_HOME}
RUN chown -R ${APP_USER}:${APP_GROUP} ${APP_HOME}

EXPOSE 8000

USER ${APP_USER}

# Entrypoint will run migrations then exec the provided command
ENTRYPOINT ["/app/docker-entrypoint.sh"]

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
