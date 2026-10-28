import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError

from friday.domain.research import SourceRecord
from friday.persistence.repositories.reports import ReportRepository

from .conftest import Seeded, SessionFactory, run_db
from .task_helpers import new_task, sql


def _source(key: str, url: str) -> SourceRecord:
    return SourceRecord(
        key=key,
        url=url,
        title=f"Title {key}",
        retrieved_at=datetime(2026, 10, 29, 12, 0, tzinfo=UTC),
        content_hash="a" * 64,
        excerpt="excerpt",
        content_type="text/html",
        size_bytes=123,
    )


def _create(factory: SessionFactory, who: Seeded, task_id: uuid.UUID | None = None, n: int = 2):  # type: ignore[no-untyped-def]
    async def go(session):  # type: ignore[no-untyped-def]
        repo = ReportRepository(session)
        report = await repo.create(
            user_id=who.user_id,
            task_id=task_id,
            question="What is X?",
            summary="X is Y.",
            content={"findings": []},
            sources=[_source(f"S{i}", f"https://example.com/{i}") for i in range(1, n + 1)],
        )
        await session.commit()
        return report

    return run_db(factory, go)


def test_report_round_trips_with_ordered_sources(
    alice: Seeded, session_factory: SessionFactory
) -> None:
    report = _create(session_factory, alice, n=11)

    async def read(session):  # type: ignore[no-untyped-def]
        return await ReportRepository(session).get_owned(report.id, alice.user_id)

    found = run_db(session_factory, read)
    assert found is not None
    loaded, sources = found
    assert (loaded.question, loaded.summary, loaded.content) == (
        "What is X?",
        "X is Y.",
        {"findings": []},
    )
    assert [s.source_key for s in sources] == [
        f"S{i}" for i in range(1, 12)
    ]  # numeric, not lexical
    assert sources[0].content_hash == "a" * 64 and sources[0].retrieved_at.year == 2026


def test_reports_are_owner_scoped_and_deletable_with_their_sources(
    alice: Seeded, bob: Seeded, session_factory: SessionFactory, test_db_url: str
) -> None:
    report = _create(session_factory, alice)

    async def attempt(session, who):  # type: ignore[no-untyped-def]
        repo = ReportRepository(session)
        return await repo.get_owned(report.id, who.user_id), await repo.delete_owned(
            report.id, who.user_id
        )

    assert run_db(session_factory, lambda s: attempt(s, bob)) == (None, False)
    assert sql(test_db_url, "SELECT count(*) FROM reports")[0][0] == 1

    async def delete(session):  # type: ignore[no-untyped-def]
        deleted = await ReportRepository(session).delete_owned(report.id, alice.user_id)
        await session.commit()
        return deleted

    assert run_db(session_factory, delete) is True
    assert sql(test_db_url, "SELECT count(*) FROM reports")[0][0] == 0
    assert sql(test_db_url, "SELECT count(*) FROM research_sources")[0][0] == 0  # cascade


def test_a_task_yields_at_most_one_report_and_survives_task_purge(
    alice: Seeded, session_factory: SessionFactory, test_db_url: str
) -> None:
    task = new_task(session_factory, alice)
    first = _create(session_factory, alice, task_id=task.id)
    with pytest.raises(IntegrityError):
        _create(session_factory, alice, task_id=task.id)

    sql(test_db_url, "DELETE FROM tasks WHERE id = %s", (task.id,))  # e.g. retention purge
    rows = sql(test_db_url, "SELECT task_id FROM reports WHERE id = %s", (first.id,))
    assert rows == [(None,)]  # the report outlives its task
