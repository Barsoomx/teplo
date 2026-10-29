from datetime import timedelta

from apps.system.services.jobs import teplo_jobs


def test_teplo_jobs_schedule() -> None:
    assert [(job.name, job.interval) for job in teplo_jobs()] == [
        ('poll_thermostats', timedelta(seconds=30)),
        ('capture_samples', timedelta(minutes=1)),
        ('run_heating_cycle', timedelta(minutes=1)),
        ('flush_samples', timedelta(minutes=5)),
    ]
