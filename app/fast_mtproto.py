from __future__ import annotations

import asyncio
import hashlib
import math
from contextlib import suppress
from pathlib import Path

from telethon import helpers, utils
from telethon.network import MTProtoSender
from telethon.tl.functions.upload import SaveBigFilePartRequest, SaveFilePartRequest
from telethon.tl.types import InputFile, InputFileBig


def connection_count(file_size: int) -> int:
    if file_size < 2 * 1024 * 1024:
        return 2
    if file_size < 8 * 1024 * 1024:
        return 4
    if file_size < 64 * 1024 * 1024:
        return 8
    if file_size < 128 * 1024 * 1024:
        return 12
    return 16


async def _new_sender(client):
    dc = await client._get_dc(client.session.dc_id)
    sender = MTProtoSender(client.session.auth_key, loggers=client._log)
    await sender.connect(
        client._connection(
            dc.ip_address,
            dc.port,
            dc.id,
            loggers=client._log,
            proxy=client._proxy,
        )
    )
    return sender


async def upload_path(client, path: Path, progress_callback=None):
    path = Path(path)
    file_size = path.stat().st_size
    if file_size <= 0:
        raise ValueError("Arquivo vazio")

    file_id = helpers.generate_random_long()
    part_size = int(utils.get_appropriated_part_size(file_size) * 1024)
    part_count = math.ceil(file_size / part_size)
    is_large = file_size > 10 * 1024 * 1024
    connections = min(connection_count(file_size), max(1, part_count))
    senders = await asyncio.gather(*(_new_sender(client) for _ in range(connections)))

    md5 = hashlib.md5(usedforsecurity=False)
    uploaded = 0

    async def send_part(sender, index: int, data: bytes):
        if is_large:
            request = SaveBigFilePartRequest(file_id, index, part_count, data)
        else:
            request = SaveFilePartRequest(file_id, index, data)
        await client._call(sender, request)

    try:
        with path.open("rb") as fh:
            part_index = 0
            while part_index < part_count:
                batch: list[tuple[int, bytes]] = []
                for _ in range(connections):
                    data = fh.read(part_size)
                    if not data:
                        break
                    if not is_large:
                        md5.update(data)
                    batch.append((part_index, data))
                    part_index += 1

                if not batch:
                    break

                await asyncio.gather(*(
                    send_part(senders[i], index, data)
                    for i, (index, data) in enumerate(batch)
                ))

                uploaded += sum(len(data) for _, data in batch)
                if progress_callback:
                    value = progress_callback(uploaded, file_size)
                    if asyncio.iscoroutine(value):
                        await value
    finally:
        await asyncio.gather(
            *(sender.disconnect() for sender in senders),
            return_exceptions=True,
        )

    if is_large:
        return InputFileBig(file_id, part_count, path.name)
    return InputFile(file_id, part_count, path.name, md5.hexdigest())
