FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg aria2 mkvtoolnix ca-certificates curl tar \
    && rm -rf /var/lib/apt/lists/*

ARG NM3U8DL_VERSION=v0.6.0-beta
RUN curl -fsSL "https://github.com/nilaoda/N_m3u8DL-RE/releases/download/${NM3U8DL_VERSION}/N_m3u8DL-RE_${NM3U8DL_VERSION}_linux-x64_20260629.tar.gz" -o /tmp/nm3u8.tar.gz \
    && tar -xzf /tmp/nm3u8.tar.gz -C /tmp \
    && find /tmp -maxdepth 2 -type f -name 'N_m3u8DL-RE' -exec install -m 0755 {} /usr/local/bin/N_m3u8DL-RE \; \
    && rm -rf /tmp/nm3u8* /tmp/N_m3u8DL-RE*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m playwright install --with-deps chromium

COPY app ./app
RUN mkdir -p /app/downloads

EXPOSE 8000
CMD ["python", "-m", "app.main", "api", "--host", "0.0.0.0", "--port", "8000"]
