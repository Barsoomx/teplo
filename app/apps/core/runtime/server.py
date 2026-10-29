import signal

import uvicorn

STOP_SIGNALS = (signal.SIGINT, signal.SIGTERM)


async def serve_asgi(host: str, port: int) -> None:
    config = uvicorn.Config(
        app='settings.asgi:application',
        host=host,
        port=port,
        lifespan='off',
        log_config=None,
        access_log=False,
    )
    # uvicorn re-raises the stop signal after its graceful shutdown; ignoring it here lets the caller clean up.
    previous = {stop_signal: signal.signal(stop_signal, signal.SIG_IGN) for stop_signal in STOP_SIGNALS}
    try:
        await uvicorn.Server(config).serve()
    finally:
        for stop_signal, handler in previous.items():
            signal.signal(stop_signal, handler)
