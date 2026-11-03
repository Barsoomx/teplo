from datetime import timedelta

from apps.system.services.jobs import shutdown_jobs, startup_jobs, teplo_jobs


def test_teplo_jobs_schedule() -> None:
    assert [(job.name, job.interval, job.delay) for job in teplo_jobs()] == [
        ('poll_thermostats', timedelta(seconds=30), timedelta(0)),
        ('poll_plugs', timedelta(seconds=30), timedelta(0)),
        ('guard_phases', timedelta(seconds=1), timedelta(0)),
        ('check_sensors', timedelta(seconds=30), timedelta(minutes=1)),
        ('capture_samples', timedelta(minutes=1), timedelta(0)),
        ('run_heating_cycle', timedelta(minutes=1), timedelta(0)),
        ('flush_samples', timedelta(minutes=5), timedelta(0)),
        ('evaluate_alerts', timedelta(minutes=1), timedelta(0)),
        ('report_outages', timedelta(minutes=1), timedelta(0)),
        ('deliver_alerts', timedelta(minutes=1), timedelta(0)),
    ]


def test_startup_journals_the_start_then_recovers_the_write_holds_then_starts_the_listeners() -> None:
    assert [(job.name, job.interval) for job in startup_jobs()] == [
        ('journal_startup', timedelta(0)),
        ('recover_write_holds', timedelta(0)),
        ('start_mqtt_listener', timedelta(0)),
        ('start_command_listener', timedelta(0)),
    ]


def test_shutdown_flushes_the_samples_then_stops_the_listeners() -> None:
    assert [(job.name, job.interval) for job in shutdown_jobs()] == [
        ('flush_samples', timedelta(0)),
        ('stop_mqtt_listener', timedelta(0)),
        ('stop_command_listener', timedelta(0)),
    ]
