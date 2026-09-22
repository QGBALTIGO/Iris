from __future__ import annotations

import asyncio
import getpass
import os

from telethon import TelegramClient
from telethon.sessions import StringSession


async def main() -> None:
    print("Iris · Gerador de sessão do userbot")
    print("A sessão gerada equivale ao login da sua conta. Guarde como segredo e nunca faça commit dela.\n")

    api_id_raw = os.getenv("TELEGRAM_API_ID") or input("API ID: ").strip()
    api_hash = os.getenv("TELEGRAM_API_HASH") or getpass.getpass("API Hash: ").strip()
    phone = input("Telefone com DDI (ex.: +55...): ").strip()

    if not api_id_raw or not api_hash or not phone:
        raise SystemExit("API ID, API Hash e telefone são obrigatórios.")

    client = TelegramClient(StringSession(), int(api_id_raw), api_hash)
    await client.connect()
    try:
        await client.send_code_request(phone)
        code = input("Código recebido no Telegram: ").strip()
        try:
            await client.sign_in(phone=phone, code=code)
        except Exception as exc:
            if "password" not in type(exc).__name__.lower() and "2fa" not in str(exc).lower():
                raise
            password = getpass.getpass("Senha da verificação em duas etapas: ")
            await client.sign_in(password=password)

        session = client.session.save()
        me = await client.get_me()
        print("\nSessão criada para:", getattr(me, "id", "?"))
        print("\nIRIS_TELEGRAM_SESSION=" + session)
        print("\nAdicione essa StringSession, API ID e API Hash somente como secrets da Railway.")
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
