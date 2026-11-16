# Runs anywhere that takes a container (Railway, Fly.io, a VPS...). Render users can ignore this.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8000
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY resume_tailor ./resume_tailor
RUN useradd --create-home app && mkdir -p /app/data && chown app /app/data
USER app

EXPOSE 8000
CMD ["sh", "-c", "uvicorn resume_tailor.server:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips '*'"]
