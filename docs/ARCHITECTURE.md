# Arquitetura do Iris

## Fluxo

1. URL recebida pelo Telegram ou API.
2. `security.validate_public_url` bloqueia localhost, IPs privados, link-local, esquemas perigosos e DNS que resolva para rede privada.
3. `SafeFetcher` faz HTTP sem seguir redirects cegamente; cada redirect é revalidado.
4. O extrator HTML coleta `video`, `audio`, `source`, imagens, links, metadados OpenGraph, JSON-LD e URLs de mídia embutidas em scripts.
5. Manifests HLS (`m3u8`) e DASH (`mpd`) são inspecionados para listar qualidades e sinalizar criptografia/DRM.
6. Em modo profundo, `yt-dlp` e Playwright complementam a detecção. O navegador observa as respostas de rede sem baixar os corpos inteiros.
7. A deduplicação remove parâmetros de tracking e combina metadados de recursos equivalentes.
8. Downloads são enfileirados. Arquivos diretos usam aria2 quando disponível, com fallback HTTPX. HLS/DASH usam N_m3u8DL-RE quando instalado ou yt-dlp como fallback.

## Segurança

- Sem suporte a `file://`, FTP ou URLs com credenciais embutidas.
- Bloqueio SSRF para loopback, redes privadas, link-local e metadata endpoints.
- Revalidação em redirects e requests disparados pelo browser.
- Limites de tamanho para HTML, manifests e downloads.
- Conteúdo sinalizado como DRM é detectado e não é baixado.
- Comandos externos usam `create_subprocess_exec` com argumentos separados, sem shell.

## Extensões previstas

- Persistência PostgreSQL/Redis para filas após reinício.
- Painel web do Iris.
- Extensão de navegador “Enviar para Iris”.
- Upload para S3/R2 quando o arquivo exceder o limite de envio do Telegram.
- Adaptadores específicos por domínio.
- Webhook do Telegram para produção em vez de polling.
