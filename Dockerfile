FROM python:3.12-slim AS build
WORKDIR /build
COPY requirements.txt .
RUN python -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

FROM python:3.12-slim
ENV PATH="/opt/venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 APP_ENV=prod
WORKDIR /srv/arena
COPY --from=build /opt/venv /opt/venv
RUN groupadd --gid 10001 arena && useradd --uid 10001 --gid arena -M arena \
    && mkdir -p instance/uploads && chown -R arena:arena instance
COPY app app
COPY schema.sql .
USER arena
EXPOSE 8000
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "--access-logfile", "-", "app:create_app()"]
