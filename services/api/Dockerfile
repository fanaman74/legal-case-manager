FROM python:3.12-slim
ARG APP_VERSION=0.1.0-dev
LABEL org.opencontainers.image.title="Case File Manager API" \
      org.opencontainers.image.version="${APP_VERSION}"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 APP_VERSION=${APP_VERSION}
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && useradd --system --uid 10001 --no-create-home app
COPY app ./app
COPY entrypoint.sh healthcheck.py ./
USER 10001
EXPOSE 8443
ENTRYPOINT ["./entrypoint.sh"]
