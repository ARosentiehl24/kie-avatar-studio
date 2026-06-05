from kie_avatar_studio.domain.models import (
    RESUMABLE_STATUSES,
    TERMINAL_STATUSES,
    JobStatus,
    VideoJob,
)


def test_job_default_status() -> None:
    job = VideoJob(
        id="job_test",
        script="hola",
        image_path="/tmp/x.png",
        prompt="ok",
        voice="EkK5I93UQWFDigLMpZcX",
    )
    assert job.status is JobStatus.QUEUED
    assert not job.is_terminal()
    assert not job.is_resumable()


def test_status_enum_complete() -> None:
    expected = {
        "queued",
        "validating",
        "uploading_image",
        "creating_audio",
        "waiting_audio",
        "creating_avatar",
        "waiting_video",
        "downloading",
        "completed",
        "failed",
        "cancelled",
    }
    assert {s.value for s in JobStatus} == expected


def test_terminal_and_resumable_sets_disjoint() -> None:
    assert TERMINAL_STATUSES.isdisjoint(RESUMABLE_STATUSES)
    assert JobStatus.COMPLETED in TERMINAL_STATUSES
    assert JobStatus.WAITING_AUDIO in RESUMABLE_STATUSES
