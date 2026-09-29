# Cloud image (Railway). The local Windows install doesn't use Docker: the
# launcher runs the same code with `python -m app.serve`.
FROM python:3.13-slim
ARG APP_VERSION=0.1.0-dev
LABEL org.opencontainers.image.title="Case File Manager API" \
      org.opencontainers.image.version="${APP_VERSION}"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 APP_VERSION=${APP_VERSION} \
    DEPLOY_MODE=cloud DATA_DIR=/data PORT=8080
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && useradd --system --uid 10001 --no-create-home app
COPY app ./app
USER 10001
EXPOSE 8080
CMD ["python", "-m", "app.serve"]
