import asyncio
import sys

CHUNK = 65536


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(CHUNK):
            writer.write(data)
            await writer.drain()
    finally:
        writer.close()


async def _serve(node: str, port: int) -> None:
    async def forward(client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter) -> None:
        try:
            node_reader, node_writer = await asyncio.open_connection(node, port)
        except OSError:
            client_writer.close()
            return

        await asyncio.gather(
            _pipe(client_reader, node_writer),
            _pipe(node_reader, client_writer),
            return_exceptions=True,
        )

    server = await asyncio.start_server(forward, '0.0.0.0', port)
    async with server:
        await server.serve_forever()


if __name__ == '__main__':
    asyncio.run(_serve(sys.argv[1], int(sys.argv[2])))
