from __future__ import annotations

from datetime import UTC, datetime, timedelta
from threading import Event

from test_api_client import job_payload

from student_schedule_worker.api.dto import JobLease
from student_schedule_worker.runtime.heartbeat import LeaseHeartbeat


def lease_expiring_at(moment: datetime) -> JobLease:
    payload = job_payload()
    payload["lease_expires_at"] = moment.isoformat()
    return JobLease.from_dict(payload)


def test_expired_lease_is_lost_without_late_heartbeat() -> None:
    class Api:
        def __init__(self) -> None:
            self.called = False

        def heartbeat(self, _lease, *, progress):
            self.called = True

    api = Api()
    heartbeat = LeaseHeartbeat(api, lease_expiring_at(datetime.now(UTC) - timedelta(seconds=1)), interval=30)
    heartbeat.start()
    assert heartbeat.lost.wait(1)
    heartbeat.stop()
    assert not api.called


def test_short_lease_renews_before_normal_interval() -> None:
    called = Event()

    class Api:
        def heartbeat(self, _lease, *, progress):
            assert progress == 5
            called.set()
            return datetime.now(UTC) + timedelta(seconds=60)

    heartbeat = LeaseHeartbeat(
        Api(),
        lease_expiring_at(datetime.now(UTC) + timedelta(seconds=0.3)),
        interval=30,
    )
    heartbeat.start()
    assert called.wait(1)
    heartbeat.stop()
    assert not heartbeat.lost.is_set()
