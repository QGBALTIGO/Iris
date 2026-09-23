# Iris

Iris é um analisador de páginas e gerenciador de mídia para Telegram/API. Ele recebe URLs, detecta vídeos, streams, áudios, imagens, páginas de capítulos e arquivos, organiza o resultado e permite downloads individuais ou em lote.

## Recursos atuais

- análise rápida de HTML e metadados;
- análise profunda paralela com Playwright + yt-dlp;
- network sniffing para mídia carregada por JavaScript;
- HLS/M3U8 e DASH/MPD com variantes de qualidade;
- identificação de plataformas como Crunchyroll, Netflix, Prime Video, Disney+, Max, Paramount+, Apple TV+ e Globoplay;
- detecção de Widevine, PlayReady, FairPlay, ClearKey, AES-128 e proteção CENC/DRM desconhecida;
- detecção de DRM e conteúdo renderizado/obfuscado;
- separação entre páginas de capítulo, imagens úteis e assets de interface;
- downloads paralelos com aria2/HTTPX e fallback automático;
- jobs com progresso, velocidade e falhas parciais;
- capítulos em PDF ou ZIP quando as imagens brutas são válidas;
- vídeos enviados como vídeo ou como arquivo;
- suporte a mídia enviada diretamente ao bot;
- userbot opcional para arquivos acima do limite configurado do Bot API;
- comandos do bot configurados automaticamente;
- comando /diagnostico para testar texto, imagem, PDF e vídeo no Telegram;
- proteção SSRF, redirects privados e limites de tamanho;
- FastAPI, Docker, Railway e GitHub Actions.

## Bot

Variáveis mínimas:

```env
IRIS_BOT_TOKEN=...
IRIS_ADMIN_ID=...
```

Para entrega de arquivos grandes por userbot:

```env
IRIS_TELEGRAM_API_ID=...
IRIS_TELEGRAM_API_HASH=...
IRIS_TELEGRAM_SESSION=...
```

Principais comandos:

- `/start`
- `/ajuda`
- `/status`
- `/jobs`
- `/userbot`
- `/diagnostico`
- `/limpar`

Execução:

```bash
python -m app.main bot
```

## API

```bash
python -m app.main api
```

Documentação automática em `/docs`.

### Analisar página

```bash
curl -X POST http://localhost:8000/api/analyze \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://example.com","deep":false}'
```

Use `deep=true` para acionar Playwright e yt-dlp em paralelo.

## Motores

O Iris escolhe automaticamente:

- imagens e arquivos simples: HTTPX;
- arquivos grandes/diretos: aria2, com fallback HTTPX;
- HLS/DASH: N_m3u8DL-RE quando disponível, depois yt-dlp;
- sites suportados pelo yt-dlp: yt-dlp;
- páginas dinâmicas: Playwright.

O projeto identifica a plataforma e o sistema de proteção quando isso aparece nos manifestos, mas não tenta obter chaves, contornar DRM ou remover controles de acesso. Streams protegidos permanecem visíveis no diagnóstico e indisponíveis para download.

## Testes

```bash
python -m compileall -q app tests
pytest -q
```

A suíte cobre HTML, JSON-LD, HLS, DASH, DRM, deduplicação, filas, downloads, SSRF, classificação de páginas, bundles e detecção de payloads de imagem inválidos/obfuscados.

Veja `docs/ARCHITECTURE.md`.
