"""Smoke unitarios de los helpers UI compartidos (`_text_format`,
`_status_badges`, `_table_helpers`)."""

from __future__ import annotations

from kie_avatar_studio.domain.models import AudioJobStatus, JobStatus
from kie_avatar_studio.ui._status_badges import (
    AUDIO_STATUS_BADGES,
    BASE_STATUS_BADGES,
    VIDEO_STATUS_BADGES,
)
from kie_avatar_studio.ui._text_format import truncate

# --- truncate --------------------------------------------------------------


def test_truncate_no_op_when_short() -> None:
    assert truncate("hola", 10) == "hola"


def test_truncate_exact_length() -> None:
    assert truncate("12345", 5) == "12345"


def test_truncate_adds_ellipsis_when_exceeds() -> None:
    assert truncate("abcdefghij", 5) == "abcd…"


def test_truncate_handles_empty() -> None:
    assert truncate("", 5) == ""


# --- status badges ---------------------------------------------------------


def test_base_status_badges_cover_shared_statuses() -> None:
    """Los status comunes a video y audio deben tener badge."""
    for shared in (JobStatus.QUEUED, JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
        assert shared.value in BASE_STATUS_BADGES


def test_video_status_badges_only_video_specific() -> None:
    """Los badges 'solo video' no se solapan con BASE ni AUDIO."""
    overlap_base = set(VIDEO_STATUS_BADGES.keys()) & set(BASE_STATUS_BADGES.keys())
    overlap_audio = set(VIDEO_STATUS_BADGES.keys()) & set(AUDIO_STATUS_BADGES.keys())
    assert overlap_base == set()
    assert overlap_audio == set()


def test_audio_status_badges_only_audio_specific() -> None:
    """Los badges 'solo audio' no se solapan con BASE."""
    overlap_base = set(AUDIO_STATUS_BADGES.keys()) & set(BASE_STATUS_BADGES.keys())
    assert overlap_base == set()


def test_video_status_badges_cover_all_video_only_states() -> None:
    """Cada status de VideoJob que NO esté en BASE debe estar en VIDEO."""
    video_only = {s.value for s in JobStatus if s.value not in BASE_STATUS_BADGES}
    assert video_only.issubset(set(VIDEO_STATUS_BADGES.keys()))


def test_audio_status_badges_cover_all_audio_only_states() -> None:
    """Cada status de AudioJob que NO esté en BASE debe estar en AUDIO."""
    audio_only = {s.value for s in AudioJobStatus if s.value not in BASE_STATUS_BADGES}
    assert audio_only.issubset(set(AUDIO_STATUS_BADGES.keys()))
