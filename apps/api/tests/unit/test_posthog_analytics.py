"""PostHog adapter: the identity that lets PostHog deduplicate a redelivered event, and what a
flush confirms. Loopback only: no external network access."""

from __future__ import annotations

import contextvars
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from queue import Queue
from typing import Any

from app.kernel.infrastructure import observability
from app.kernel.infrastructure.config import CoreSettings
from app.kernel.infrastructure.observability import PostHogAnalytics
from tests.stub_server import Recorded, stub_server

USER = uuid.uuid4()
OCCURRED_AT = datetime(2026, 6, 1, 14, 0, tzinfo=timezone(timedelta(hours=2)))


class FakeClient:
    """Stands in for a PostHog client. ``admit=False`` refuses every event, as a full queue does."""

    def __init__(self, *, admit: bool = True) -> None:
        self.captures: list[dict[str, Any]] = []
        self.queue: Queue[Any] = Queue()
        self.admit = admit

    def capture(self, event: str, **kwargs: Any) -> str | None:
        self.captures.append({"event": event, **kwargs})
        return str(uuid.uuid4()) if self.admit else None

    def flush(self, timeout_seconds: float) -> None:
        return None

    def shutdown(self) -> None:
        return None


class Unanswered(FakeClient):
    """A sender PostHog does not answer: each send waits until ``answer`` is set."""

    def __init__(self) -> None:
        super().__init__()
        self.started: Queue[str] = Queue()  # the uuid of each send, as it starts
        self.answer = threading.Event()

    def capture(self, event: str, **kwargs: Any) -> str | None:
        self.started.put(kwargs["uuid"])
        self.answer.wait(10)
        return super().capture(event, **kwargs)


def with_clients(queued: FakeClient, sent: FakeClient | None = None, **options: Any) -> PostHogAnalytics:
    """The adapter, with fakes for its client that queues events and its client that sends one at once."""
    analytics = PostHogAnalytics(api_key="phc_test_only", host="http://127.0.0.1:9", **options)
    analytics._client = queued  # type: ignore[assignment]
    analytics._sender = sent or queued  # type: ignore[assignment]
    return analytics


def handle(analytics: PostHogAnalytics, event_id: uuid.UUID | None = None) -> bool:
    """What an outbox handler does: send its event with the outbox event's ID, then confirm it."""
    analytics.capture(USER, "plan_changed", {"plan": "pro"}, event_id=event_id or uuid.uuid4(), occurred_at=OCCURRED_AT)
    return analytics.flush()


def sent_uuids(sender: FakeClient, analytics: PostHogAnalytics) -> set[str]:
    """The uuid of every event the sender was given, once every send still running has finished."""
    analytics._sending.shutdown(wait=True)
    return {c["uuid"] for c in sender.captures}


def largest_worker_pool() -> int:
    """The upper bound of DATABASE_WORKER_POOL_MAX_SIZE."""
    field = CoreSettings.model_fields["database_worker_pool_max_size"]
    return int(next(m.le for m in field.metadata if hasattr(m, "le")))


def test_an_outbox_capture_is_sent_on_its_own_with_the_event_id_and_occurrence_time() -> None:
    queued, sent = FakeClient(), FakeClient()
    event_id = uuid.uuid4()
    analytics = with_clients(queued, sent)
    analytics.capture(USER, "plan_changed", {"plan": "pro"}, event_id=event_id, occurred_at=OCCURRED_AT)
    assert queued.captures == []
    assert sent.captures == [
        {
            "event": "plan_changed",
            "distinct_id": str(USER),
            "properties": {"plan": "pro"},
            "uuid": str(event_id),
            "timestamp": OCCURRED_AT,
        }
    ]
    assert analytics.flush() is True


def test_a_capture_without_an_event_id_is_queued() -> None:
    """The request path never waits for PostHog (OBS-06)."""
    queued, sent = FakeClient(), FakeClient()
    with_clients(queued, sent).capture(USER, "checkout_started", {"plan": "pro"})
    assert [c["event"] for c in queued.captures] == ["checkout_started"]
    assert sent.captures == []


def test_a_queued_capture_keeps_the_time_it_occurred() -> None:
    """Without an occurrence time, PostHog stamps the event with the current time."""
    queued = FakeClient()
    analytics = with_clients(queued, FakeClient())
    analytics.capture(USER, "checkout_started", {"plan": "pro"}, occurred_at=OCCURRED_AT)
    analytics.capture(USER, "checkout_started", {"plan": "pro"})
    assert [c["timestamp"] for c in queued.captures] == [OCCURRED_AT, None]


def test_a_redelivery_is_the_same_posthog_event_on_the_wire() -> None:
    """PostHog deduplicates an event by its uuid together with its event, distinct ID, and timestamp."""
    event_id = uuid.uuid4()
    with stub_server(lambda req: (200, {"status": 1})) as stub:
        analytics = PostHogAnalytics(api_key="phc_test_only", host=stub.url, flush_timeout_seconds=10)
        try:
            for _ in range(2):  # the first delivery, then a redelivery of the same outbox event
                analytics.capture(USER, "plan_changed", {"plan": "pro"}, event_id=event_id, occurred_at=OCCURRED_AT)
                assert analytics.flush() is True
            analytics.capture(USER, "plan_changed", {"plan": "pro"})
            assert analytics.flush() is True
        finally:
            analytics.shutdown()
    sent = [e for r in stub.requests if r.method == "POST" for e in r.json().get("batch", [])]
    identities = [(e["event"], e["distinct_id"], e["uuid"], e["timestamp"]) for e in sent]
    redelivered = ("plan_changed", str(USER), str(event_id), "2026-06-01T12:00:00+00:00")
    assert identities[:2] == [redelivered, redelivered]
    assert identities[2][2] != str(event_id)  # without an event ID, PostHog sees a new event


def test_a_handler_confirms_its_own_event_while_requests_keep_queueing() -> None:
    """Request-path captures keep the shared queue from draining. An outbox handler's flush
    confirms its own event without waiting for theirs."""
    received: set[str] = set()

    def healthy(request: Recorded) -> tuple[int, Any]:
        time.sleep(0.15)  # a healthy PostHog round trip
        received.update(e["uuid"] for e in request.json().get("batch", []))
        return 200, {"status": 1}

    with stub_server(healthy) as stub:
        analytics = PostHogAnalytics(api_key="phc_test_only", host=stub.url)
        flowing, stop = threading.Event(), threading.Event()

        def requests_capturing() -> None:  # 50 a second, never flushed, as checkout_started is
            captured = 0
            while not stop.wait(1 / 50):
                analytics.capture(uuid.uuid4(), "checkout_started", {"plan": "pro"})
                captured += 1
                if captured == 5:
                    flowing.set()

        traffic = threading.Thread(target=requests_capturing)
        traffic.start()
        event_id = uuid.uuid4()
        try:
            assert flowing.wait(10)
            started = time.monotonic()
            analytics.capture(USER, "plan_changed", {"plan": "pro"}, event_id=event_id, occurred_at=OCCURRED_AT)
            confirmed = analytics.flush()
            elapsed = time.monotonic() - started
        finally:
            stop.set()
            traffic.join(5)
            analytics.shutdown()
    assert confirmed is True
    assert elapsed < 1, f"the handler waited {elapsed:.1f} s"
    assert str(event_id) in received


def test_a_posthog_that_does_not_answer_holds_one_handler_per_cooldown() -> None:
    """An outbox handler waits at most the confirm timeout (2 s), well below the time an
    after-response dispatch waits for a slot (DATABASE_CONNECT_TIMEOUT_SECONDS, 5 s by default).
    The handlers after it, until the cooldown is over, are not held at all: their events are
    reported unconfirmed without being sent."""
    arrived, answer = threading.Event(), threading.Event()

    def stalled(_request: Recorded) -> tuple[int, Any]:
        arrived.set()
        answer.wait(10)
        return 200, {"status": 1}

    with stub_server(stalled) as stub:
        analytics = PostHogAnalytics(api_key="phc_test_only", host=stub.url)
        started = time.monotonic()
        try:
            confirmed = [handle(analytics) for _ in range(3)]
            elapsed = time.monotonic() - started
            assert arrived.wait(5)  # the stub was reached: PostHog just had not answered yet
        finally:
            answer.set()
            analytics.shutdown()
    assert confirmed == [False] * 3
    assert elapsed < 3, f"the handlers waited {elapsed:.1f} s"
    assert len(stub.requests) == 1


def test_posthog_is_given_longer_to_answer_than_a_handler_waits() -> None:
    """Otherwise the SDK could give up on a PostHog that does not answer just before the handler
    does: the send would look rejected, not unanswered, and no cooldown would start."""
    analytics = PostHogAnalytics(api_key="phc_test_only", host="http://127.0.0.1:9", confirm_timeout_seconds=0.5)
    try:
        assert analytics._sender.timeout > 0.5
    finally:
        analytics.shutdown()


def test_a_send_that_outlasts_its_own_timeout_is_given_up_in_time() -> None:
    """The SDK's timeout bounds a single attempt: it retries a failed connection and tries each of
    the host's addresses in turn. The wait for a handler's send is bounded on its own."""
    release = threading.Event()

    class Stalled(FakeClient):
        def capture(self, event: str, **kwargs: Any) -> str | None:
            release.wait(10)
            return super().capture(event, **kwargs)

    analytics = with_clients(FakeClient(), Stalled(), confirm_timeout_seconds=0.2)
    started = time.monotonic()
    try:
        analytics.capture(USER, "plan_changed", {"plan": "pro"}, event_id=uuid.uuid4(), occurred_at=OCCURRED_AT)
        confirmed = analytics.flush()
        elapsed = time.monotonic() - started
    finally:
        release.set()
        analytics.shutdown()
    assert confirmed is False
    assert elapsed < 2, f"the handler waited {elapsed:.1f} s"


def test_while_posthog_does_not_answer_sends_fail_fast_until_it_answers_a_trial() -> None:
    """After a send PostHog does not answer within the confirm timeout, the next ones are reported
    unconfirmed at once, without being sent, so a hanging PostHog cannot hold the after-response
    dispatch slots handler after handler; the outbox retries the events. Once the cooldown is over,
    one trial send goes through, and the others still fail fast until PostHog answers it."""
    now = [0.0]
    sender = Unanswered()
    analytics = with_clients(FakeClient(), sender, confirm_timeout_seconds=1, cooldown_seconds=30, clock=lambda: now[0])
    unanswered, failed_fast, trial, beside_the_trial, resumed = (uuid.uuid4() for _ in range(5))
    outcome: dict[str, bool] = {}
    in_flight = threading.Thread(target=lambda: outcome.update(trial=handle(analytics, trial)))
    try:
        assert handle(analytics, unanswered) is False  # waited out the confirm timeout
        assert handle(analytics, failed_fast) is False
        now[0] += 30
        in_flight.start()
        assert sender.started.get(timeout=5) == str(unanswered)
        assert sender.started.get(timeout=5) == str(trial)
        assert handle(analytics, beside_the_trial) is False
        sender.answer.set()  # PostHog answers the trial in time
        in_flight.join(5)
        assert handle(analytics, resumed) is True
    finally:
        sender.answer.set()
    assert outcome == {"trial": True}
    assert sent_uuids(sender, analytics) == {str(unanswered), str(trial), str(resumed)}


def test_a_trial_send_posthog_does_not_answer_starts_another_cooldown() -> None:
    now = [0.0]
    sender = Unanswered()
    analytics = with_clients(
        FakeClient(), sender, confirm_timeout_seconds=0.2, cooldown_seconds=30, clock=lambda: now[0]
    )
    unanswered, trial, failed_fast, next_trial = (uuid.uuid4() for _ in range(4))
    try:
        assert handle(analytics, unanswered) is False
        now[0] += 30
        assert handle(analytics, trial) is False  # sent, and again not answered in time
        now[0] += 29
        assert handle(analytics, failed_fast) is False
        now[0] += 1
        sender.answer.set()
        assert handle(analytics, next_trial) is True
    finally:
        sender.answer.set()
    assert sent_uuids(sender, analytics) == {str(unanswered), str(trial), str(next_trial)}


def test_a_send_still_waiting_for_a_thread_when_its_handler_gives_up_is_never_made() -> None:
    """While every sending thread is taken, a further send waits for one. If its handler's confirm
    timeout ends first, the send is dropped (the outbox retries the event), so a long outage never
    builds up stale sends that all fire once PostHog answers again."""
    sender = Unanswered()
    # No cooldown: every send is attempted, so this one is submitted while the others hold the threads.
    analytics = with_clients(FakeClient(), sender, confirm_timeout_seconds=0.2, cooldown_seconds=0)
    taken = [uuid.uuid4() for _ in range(observability._SENDING_THREADS)]
    handlers = [threading.Thread(target=handle, args=(analytics, event_id)) for event_id in taken]
    late = uuid.uuid4()
    try:
        for thread in handlers:
            thread.start()
        assert {sender.started.get(timeout=5) for _ in taken} == {str(e) for e in taken}
        assert handle(analytics, late) is False
    finally:
        sender.answer.set()
        for thread in handlers:
            thread.join(5)
    assert sent_uuids(sender, analytics) == {str(e) for e in taken}


def test_every_handler_that_can_run_at_once_sends_at_once() -> None:
    """Per process, up to DATABASE_WORKER_POOL_MAX_SIZE - 1 after-response dispatches and a
    drain-outbox run can each be sending an event. None waits for another's send to finish, so
    none is given up while PostHog answers in time."""
    dispatches = largest_worker_pool() - 1
    at_once = dispatches + 1
    together = threading.Barrier(at_once)

    class AnswersOnceAllAreSending(FakeClient):
        def capture(self, event: str, **kwargs: Any) -> str | None:
            together.wait(5)
            return super().capture(event, **kwargs)

    analytics = with_clients(FakeClient(), AnswersOnceAllAreSending(), confirm_timeout_seconds=5)
    confirmed: list[bool] = []
    handlers = [threading.Thread(target=lambda: confirmed.append(handle(analytics))) for _ in range(at_once)]
    for thread in handlers:
        thread.start()
    for thread in handlers:
        thread.join(10)
    assert confirmed == [True] * at_once


def test_an_event_posthog_rejects_is_not_confirmed() -> None:
    with stub_server(lambda req: (500, {"error": "down"})) as stub:
        analytics = PostHogAnalytics(api_key="phc_test_only", host=stub.url)
        try:
            analytics.capture(USER, "plan_changed", {"plan": "pro"}, event_id=uuid.uuid4(), occurred_at=OCCURRED_AT)
            assert analytics.flush() is False
            assert analytics.flush() is True  # reported once
        finally:
            analytics.shutdown()
    assert [r.path for r in stub.requests] == ["/batch/"]


def test_a_handler_that_sends_several_events_needs_every_one_accepted() -> None:
    sent = FakeClient(admit=False)
    analytics = with_clients(FakeClient(), sent)
    analytics.capture(USER, "plan_changed", {"plan": "pro"}, event_id=uuid.uuid4(), occurred_at=OCCURRED_AT)
    sent.admit = True
    analytics.capture(USER, "project_created", {"context": "personal"}, event_id=uuid.uuid4(), occurred_at=OCCURRED_AT)
    assert analytics.flush() is False


def test_after_shutdown_a_handlers_event_is_not_confirmed() -> None:
    sent = FakeClient()
    analytics = with_clients(FakeClient(), sent)
    analytics.shutdown()
    analytics.capture(USER, "plan_changed", {"plan": "pro"}, event_id=uuid.uuid4(), occurred_at=OCCURRED_AT)
    assert analytics.flush() is False
    assert sent.captures == []


def test_an_event_the_queue_refuses_fails_the_next_flush() -> None:
    """A refused event is never exported, so the flush that follows (the integrations check's)
    cannot confirm it."""
    with stub_server(lambda req: (200, {"status": 1})) as stub:
        analytics = PostHogAnalytics(api_key="phc_test_only", host=stub.url, flush_timeout_seconds=5)
        analytics._client.shutdown()  # the queue now refuses every event
        analytics.capture(USER, "integration_test_sent", {"source": "api"})
        assert analytics.flush() is False
        assert analytics.flush() is True  # reported once
    assert stub.requests == []


def test_a_refusal_fails_only_the_flush_on_the_thread_that_captured() -> None:
    """Concurrent handlers share the client: another thread's flush neither fails because of this
    refusal nor hides it from the flush of the handler whose event was refused."""
    analytics = with_clients(FakeClient(admit=False))
    captured, other_flushed = threading.Event(), threading.Event()
    results: dict[str, bool] = {}

    def refused_handler() -> None:
        analytics.capture(USER, "plan_changed", {"plan": "pro"}, event_id=uuid.uuid4(), occurred_at=OCCURRED_AT)
        captured.set()
        other_flushed.wait(5)
        results["refused"] = analytics.flush()

    thread = threading.Thread(target=refused_handler)
    thread.start()
    try:
        assert captured.wait(5)
        results["other"] = analytics.flush()
    finally:
        other_flushed.set()
        thread.join(5)
    assert results == {"other": True, "refused": False}


def test_a_refused_request_path_capture_never_fails_a_handlers_flush() -> None:
    """A handler's flush confirms what it sent, and only that: a refused capture without an event
    ID, never flushed, made before it in the same caller, is not the handler's event."""
    client = FakeClient(admit=False)
    analytics = with_clients(client)
    analytics.capture(uuid.uuid4(), "checkout_started", {"plan": "pro"})  # refused, never flushed
    client.admit = True
    analytics.capture(USER, "plan_changed", {"plan": "pro"}, event_id=uuid.uuid4(), occurred_at=OCCURRED_AT)
    assert analytics.flush() is True


def test_what_one_caller_captured_never_decides_another_callers_flush() -> None:
    """Each request, after-response dispatch, and job run gets a context of its own, even on a
    reused worker thread (anyio copies one per call). An earlier caller that never flushed leaves
    nothing behind: not an unconfirmed send, not a refused capture."""
    client = FakeClient(admit=False)
    analytics = with_clients(client)

    def earlier_caller() -> None:
        analytics.capture(USER, "plan_changed", {"plan": "pro"}, event_id=uuid.uuid4(), occurred_at=OCCURRED_AT)
        analytics.capture(uuid.uuid4(), "checkout_started", {"plan": "pro"})

    def integrations_check() -> bool:
        analytics.capture(USER, "integration_test_sent", {"source": "api"})
        return analytics.flush()

    contextvars.copy_context().run(earlier_caller)  # both refused, never flushed
    client.admit = True
    assert contextvars.copy_context().run(integrations_check) is True


def test_a_refused_capture_fails_the_flush_even_if_later_ones_were_queued() -> None:
    client = FakeClient(admit=False)
    analytics = with_clients(client)
    analytics.capture(USER, "integration_test_sent", {"source": "api"})
    client.admit = True
    analytics.capture(USER, "integration_test_sent", {"source": "api"})
    assert analytics.flush() is False
    assert analytics.flush() is True  # reported once


def test_a_filtered_event_is_not_a_refusal() -> None:
    """An event outside the allowlist is dropped by policy (OBS-03), not lost: the flush still confirms."""
    client = FakeClient(admit=False)
    analytics = with_clients(client)
    analytics.capture(USER, "user_email_changed", {"email": "a@example.com"})
    assert analytics.flush() is True
    analytics.capture(USER, "user_email_changed", {"email": "a@example.com"}, event_id=uuid.uuid4())
    assert analytics.flush() is True
    assert client.captures == []
