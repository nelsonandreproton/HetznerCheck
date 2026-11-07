"""Container state alerts need 2 consecutive bad readings, so transient states
during deploys (created/restarting/exited between recreate steps) don't alert."""
from monitor.checker import ThresholdChecker


def _metrics(status="running", health=None):
    return {
        "cpu": {"percent": 1, "load_1": 0.1, "cpu_count": 2},
        "memory": {"percent": 10, "swap_percent": 0, "swap_total": 0},
        "disk": {},
        "ssh": {"failures_last_hour": 0},
        "docker": [{"name": "web", "status": status, "health": health}],
        "uptime": {"boot_time": 1.0, "uptime_human": "1m"},
        "processes": {"zombie_count": 0},
    }


def _docker_alerts(alerts):
    return [a for a in alerts if "web" in str(a)]


def test_container_down_single_reading_no_alert():
    c = ThresholdChecker({})
    assert _docker_alerts(c.check(_metrics("created"))) == []


def test_container_down_two_consecutive_readings_alerts_once():
    c = ThresholdChecker({})
    c.check(_metrics("exited"))
    assert len(_docker_alerts(c.check(_metrics("exited")))) == 1
    assert _docker_alerts(c.check(_metrics("exited"))) == []  # cooldown


def test_container_recovers_between_readings_no_alert():
    c = ThresholdChecker({})
    c.check(_metrics("created"))
    c.check(_metrics("running"))
    assert _docker_alerts(c.check(_metrics("created"))) == []


def test_container_unhealthy_needs_two_readings():
    c = ThresholdChecker({})
    assert _docker_alerts(c.check(_metrics("running", "unhealthy"))) == []
    assert len(_docker_alerts(c.check(_metrics("running", "unhealthy")))) == 1
