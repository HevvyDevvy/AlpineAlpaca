# Headless, reproducible build — runs identically whether it's tested in
# CI, on your own server, or on a host like Railway/Render/Fly.io.

FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# SECRET_KEY and MASTER_KEY must be supplied at runtime (docker run -e ... or
# your host's environment variable settings) — never baked into the image.
ENV PORT=8080
EXPOSE 8080

CMD ["gunicorn", "-w", "2", "-b", "0.0.0.0:8080", "app:create_app()"]
