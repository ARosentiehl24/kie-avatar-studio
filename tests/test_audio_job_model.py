"""Tests del modelo `AudioJob`: defaults + métodos terminal/resumable."""

from __future__ import annotations

from kie_avatar_studio.domain.models import AudioJob, AudioJobStatus


def _job(status: AudioJobStatus = AudioJobStatus.QUEUED) -> AudioJob:
    return AudioJob(
        id="aud_x",
        label="X",
        script="Hola",
        voice_id="EkK5I93UQWFDigLMpZcX",
        status=status,
    )


def test_defaults() -> None:
    j = AudioJob(id="aud_1", label="X", script="Hola", voice_id="EkK5I93UQWFDigLMpZcX")
    assert j.status == AudioJobStatus.QUEUED
    assert j.task_id is None
    assert j.kie_url is None
    assert j.kie_file_path is None
    assert j.error is None
    assert j.voice_settings_json is None
    assert j.created_at is not None
    assert j.updated_at is not None


def test_is_terminal_true_for_terminal_states() -> None:
    for s in (AudioJobStatus.COMPLETED, AudioJobStatus.FAILED, AudioJobStatus.CANCELLED):
        assert _job(s).is_terminal() is True


def test_is_terminal_false_for_in_progress() -> None:
    for s in (
        AudioJobStatus.QUEUED,
        AudioJobStatus.VALIDATING,
        AudioJobStatus.CREATING,
        AudioJobStatus.POLLING,
    ):
        assert _job(s).is_terminal() is False


def test_is_resumable_true_for_queued_and_polling() -> None:
    assert _job(AudioJobStatus.QUEUED).is_resumable() is True
    assert _job(AudioJobStatus.POLLING).is_resumable() is True


def test_is_resumable_false_for_creating_and_terminal() -> None:
    # CREATING queda excluido a propósito: sin task_id persistido no podemos
    # saber si el POST llegó a crear el task en Kie.
    assert _job(AudioJobStatus.CREATING).is_resumable() is False
    for s in (AudioJobStatus.COMPLETED, AudioJobStatus.FAILED, AudioJobStatus.CANCELLED):
        assert _job(s).is_resumable() is False
