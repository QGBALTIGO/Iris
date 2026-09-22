# Iris

Iris é um analisador universal de páginas e gerenciador de downloads para Telegram/API. A ideia é receber uma URL, detectar vídeos, streams, áudios, imagens, documentos e outros arquivos, mostrar o que foi encontrado e permitir downloads individuais ou em lote.

## O que já existe

- análise rápida de HTML;
- OpenGraph, JSON-LD, `video`, `audio`, `source`, `srcset` e links diretos;
- detecção de URLs de mídia embutidas em scripts;
- HLS/M3U8 com variantes de qualidade;
- DASH/MPD com representações;
- detecção e bloqueio de DRM;
- análise profunda opcional com yt-dlp e Playwright/network sniffing;
- deduplicação de recursos;
- API FastAPI;
- bot Telegram preparado para token/admin;
- download em lote e acompanhamento por jobs;
- aria2 para downloads diretos quando disponível;
- yt-dlp/N_m3u8DL-RE como motores de streams;
- fallback HTTPX;
- proteção SSRF e limites de tamanho;
- Docker e GitHub Actions.

## Execução local

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
python -m playwright install chromium
cp .env.example .env
pytest -q
python -m app.main api
```

API em `http://localhost:8000` e documentação em `/docs`.

### Bot

Preencha no `.env`:

```env
IRIS_BOT_TOKEN=...
IRIS_ADMIN_ID=...
```

Depois:

```bash
python -m app.main bot
```

O usuário envia uma URL. O Iris faz a análise rápida e oferece uma análise profunda e download em lote dos vídeos/streams detectados.

## API

### Analisar página

```bash
curl -X POST http://localhost:8000/api/analyze \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://example.com","deep":false}'
```

Use `deep=true` para acionar yt-dlp e Playwright.

### Criar lote

`POST /api/downloads` recebe uma lista de recursos retornados pela análise. Consulte o progresso em `GET /api/jobs/{id}`.

## Motores

O Iris escolhe automaticamente:

- arquivo direto: `aria2c` -> HTTPX;
- HLS/DASH: `N_m3u8DL-RE` -> `yt-dlp`;
- análise por site: `yt-dlp`;
- página dinâmica: Playwright.

O projeto não tenta contornar DRM ou controles de acesso.

## Testes

```bash
ruff check app tests
pytest -q
```

Os testes cobrem classificação de arquivos, HTML, JSON-LD, HLS, DASH, DRM, deduplicação, filas, API, nomes de arquivo e bloqueios SSRF.

Veja [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
