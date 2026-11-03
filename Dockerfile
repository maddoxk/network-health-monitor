FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir . && useradd --system --no-create-home nhm
USER nhm
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/health', timeout=2)"
ENTRYPOINT ["nethealth"]
CMD ["--config", "/config/targets.yaml", "--listen", "0.0.0.0:8000"]
