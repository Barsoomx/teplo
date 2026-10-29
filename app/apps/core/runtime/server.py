import uvicorn


async def serve_asgi(host: str, port: int) -> None:
    config = uvicorn.Config(
        app='settings.asgi:application',
        host=host,
        port=port,
        lifespan='off',
        log_config=None,
        access_log=False,
    )
    await uvicorn.Server(config).serve()
