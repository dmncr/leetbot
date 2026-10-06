FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    DATABASE_PATH=/data/leetbot.sqlite3 LEGACY_SCORES_PATH=/legacy/scores.json
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && groupadd --gid 1000 leetbot \
    && useradd --uid 1000 --gid leetbot --no-create-home leetbot \
    && mkdir -p /data /legacy && chown leetbot:leetbot /data
COPY leetbot.py game.py storage.py analytics.py webapp.py ./
COPY templates ./templates
COPY static ./static
USER 1000:1000
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3)"
CMD ["python", "leetbot.py"]

