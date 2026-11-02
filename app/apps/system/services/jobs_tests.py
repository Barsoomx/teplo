from datetime import timedelta

from apps.system.services.jobs import teplo_jobs


def test_teplo_jobs_schedule() -> None:
    assert [(job.name, job.interval, job.delay) for job in teplo_jobs()] == [
        ('poll_thermostats', timedelta(seconds=30), timedelta(0)),
        ('poll_plugs', timedelta(seconds=30), timedelta(0)),
        ('check_sensors', timedelta(seconds=30), timedelta(minutes=1)),
        ('capture_samples', timedelta(minutes=1), timedelta(0)),
        ('run_heating_cycle', timedelta(minutes=1), timedelta(0)),
        ('flush_samples', timedelta(minutes=5), timedelta(0)),
        ('evaluate_alerts', timedelta(minutes=1), timedelta(0)),
        ('report_outages', timedelta(minutes=1), timedelta(0)),
        ('deliver_alerts', timedelta(minutes=1), timedelta(0)),
    ]
