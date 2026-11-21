# stage 1: build
FROM python:3.12-slim AS build
WORKDIR /src
# pyproject.toml uses SPEC.md as the package readme, so hatchling needs it.
COPY pyproject.toml SPEC.md ./
COPY src ./src
RUN pip wheel --no-cache-dir --wheel-dir /wheels .

# stage 2: runtime
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PORT=8080
RUN useradd --system --uid 10001 --home /app app \
 && mkdir -p /data /app/static && chown app:app /data
COPY --from=build /wheels /wheels
RUN pip install --no-cache-dir /wheels/* && rm -rf /wheels
COPY static /app/static
WORKDIR /app
VOLUME /data
EXPOSE 8080
USER app
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import os,urllib.request;urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8080\")}/healthz',timeout=4)"
CMD ["python", "-m", "domain_cert_monitor"]
