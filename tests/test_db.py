from kie_avatar_studio.domain.models import JobStatus, VideoJob
from kie_avatar_studio.infra.db import JobsDB


def _job(**overrides) -> VideoJob:
    base = dict(
        id="job_1",
        script="hola",
        image_path="/tmp/m.png",
        prompt="prompt",
        voice="V",
    )
    base.update(overrides)
    return VideoJob(**base)


async def test_init_creates_schema(jobs_db: JobsDB) -> None:
    # init ya corrió en la fixture; verificamos que upsert funciona
    await jobs_db.upsert(_job())
    fetched = await jobs_db.get("job_1")
    assert fetched is not None
    assert fetched.id == "job_1"


async def test_get_missing_returns_none(jobs_db: JobsDB) -> None:
    assert await jobs_db.get("no_existe") is None


async def test_list_recent_orders_desc(jobs_db: JobsDB) -> None:
    await jobs_db.upsert(_job(id="a"))
    await jobs_db.upsert(_job(id="b"))
    await jobs_db.upsert(_job(id="c"))
    ids = [j.id for j in await jobs_db.list_recent()]
    assert set(ids) == {"a", "b", "c"}


async def test_list_by_status(jobs_db: JobsDB) -> None:
    await jobs_db.upsert(_job(id="a", status=JobStatus.WAITING_VIDEO))
    await jobs_db.upsert(_job(id="b", status=JobStatus.COMPLETED))
    await jobs_db.upsert(_job(id="c", status=JobStatus.WAITING_VIDEO))
    waiting = await jobs_db.list_by_status(JobStatus.WAITING_VIDEO)
    assert {j.id for j in waiting} == {"a", "c"}


async def test_delete(jobs_db: JobsDB) -> None:
    await jobs_db.upsert(_job())
    await jobs_db.delete("job_1")
    assert await jobs_db.get("job_1") is None


async def test_upsert_updates_existing(jobs_db: JobsDB) -> None:
    await jobs_db.upsert(_job(status=JobStatus.QUEUED))
    await jobs_db.upsert(_job(status=JobStatus.COMPLETED, output_path="/tmp/x.mp4"))
    fetched = await jobs_db.get("job_1")
    assert fetched is not None
    assert fetched.status is JobStatus.COMPLETED
    assert fetched.output_path == "/tmp/x.mp4"
