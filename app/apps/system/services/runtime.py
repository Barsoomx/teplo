import asyncio
from datetime import timedelta

from apps.core.runtime.periodic import JobRunner, PeriodicJob
from apps.core.runtime.server import serve_asgi
from apps.devices.services.mqtt_listener import mqtt_listener
from apps.system.services.jobs import flush_samples, journal_startup, teplo_jobs

ONCE = timedelta(0)


async def run_teplo(host: str, port: int) -> None:
    runner = JobRunner.init()
    await runner.run_once(PeriodicJob(name='journal_startup', interval=ONCE, action=journal_startup))
    await runner.run_once(PeriodicJob(name='start_mqtt_listener', interval=ONCE, action=mqtt_listener.start))
    tasks = [asyncio.create_task(runner.run_forever(job)) for job in teplo_jobs()]
    try:
        await serve_asgi(host=host, port=port)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await runner.run_once(PeriodicJob(name='flush_samples', interval=ONCE, action=flush_samples))
        await runner.run_once(PeriodicJob(name='stop_mqtt_listener', interval=ONCE, action=mqtt_listener.stop))
