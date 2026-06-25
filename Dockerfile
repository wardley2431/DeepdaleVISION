FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV APP_HOST=0.0.0.0
ENV APP_PORT=8080

WORKDIR /app

COPY server.py ./
COPY static ./static

RUN useradd --create-home --shell /usr/sbin/nologin deepdalevision \
    && chown -R deepdalevision:deepdalevision /app

USER deepdalevision

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=2).read()"

CMD ["python", "server.py"]
