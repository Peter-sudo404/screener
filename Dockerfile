FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    SCREENER_HOST=0.0.0.0 \
    SCREENER_NO_BROWSER=1

# Usuario sin privilegios; su home guarda la caché de yfinance
RUN useradd --create-home --uid 1000 app
WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY --chown=app:app *.py ./
COPY --chown=app:app web/ ./web/

# Directorios escribibles: se montan como volúmenes y heredan este dueño la primera vez
RUN mkdir -p /app/.cache_edgar /app/salidas && chown -R app:app /app
USER app

EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/progress', timeout=3)"

CMD ["python", "server.py"]
