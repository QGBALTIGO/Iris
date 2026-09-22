# Railway deployment

O serviço de produção do Iris acompanha a branch `main`.

Configuração esperada:

- comando: `python -m app.main bot`;
- Playwright/Chromium habilitado;
- yt-dlp e aria2 disponíveis;
- token do bot e credenciais sensíveis somente em variáveis da Railway;
- downloads temporários limpos após entrega;
- userbot habilitado somente quando API ID, API hash e StringSession forem configurados.

Para validar a entrega no Telegram, use `/diagnostico`.

## Userbot

Gere a StringSession localmente com:

```bash
python scripts/generate_userbot_session.py
```

Cadastre API ID, API Hash e StringSession somente como secrets da Railway.
