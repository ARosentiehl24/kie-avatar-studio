"""Tests del `UpdateChecker`: parseo SemVer + lógica de comparación."""

from __future__ import annotations

from kie_avatar_studio.app_layer.update_checker import UpdateChecker
from kie_avatar_studio.domain.models import GitHubRelease


def _release(tag: str = "v1.2.3") -> GitHubRelease:
    return GitHubRelease(
        tag_name=tag,
        html_url=f"https://github.com/x/y/releases/tag/{tag}",
        body="notas",
        published_at="2026-06-05T00:00:00Z",
    )


async def test_returns_none_when_fetcher_returns_none() -> None:
    async def fetcher() -> GitHubRelease | None:
        return None

    checker = UpdateChecker(current_version="1.0.0", fetch_latest=fetcher)
    assert await checker.check() is None


async def test_returns_none_when_versions_match() -> None:
    async def fetcher() -> GitHubRelease | None:
        return _release("v1.0.0")

    checker = UpdateChecker(current_version="1.0.0", fetch_latest=fetcher)
    assert await checker.check() is None


async def test_returns_none_when_latest_is_older() -> None:
    async def fetcher() -> GitHubRelease | None:
        return _release("v0.9.0")

    checker = UpdateChecker(current_version="1.0.0", fetch_latest=fetcher)
    assert await checker.check() is None


async def test_detects_patch_update() -> None:
    async def fetcher() -> GitHubRelease | None:
        return _release("v1.0.1")

    checker = UpdateChecker(current_version="1.0.0", fetch_latest=fetcher)
    result = await checker.check()
    assert result is not None
    assert result.latest_version == "v1.0.1"
    assert result.current_version == "1.0.0"
    assert "github.com" in result.release_url


async def test_detects_minor_update() -> None:
    async def fetcher() -> GitHubRelease | None:
        return _release("v1.5.0")

    checker = UpdateChecker(current_version="1.4.7", fetch_latest=fetcher)
    result = await checker.check()
    assert result is not None
    assert result.latest_version == "v1.5.0"


async def test_detects_major_update() -> None:
    async def fetcher() -> GitHubRelease | None:
        return _release("v2.0.0")

    checker = UpdateChecker(current_version="1.99.99", fetch_latest=fetcher)
    result = await checker.check()
    assert result is not None
    assert result.latest_version == "v2.0.0"


async def test_handles_tag_without_v_prefix() -> None:
    """Tags publicados sin 'v' (raro pero permitido) se comparan igual."""

    async def fetcher() -> GitHubRelease | None:
        return _release("1.1.0")

    checker = UpdateChecker(current_version="1.0.0", fetch_latest=fetcher)
    result = await checker.check()
    assert result is not None
    assert result.latest_version == "1.1.0"


async def test_handles_prerelease_suffix() -> None:
    """Sufijo '-rc.N' se ignora — equivale a la base. CR-13.3."""

    async def fetcher() -> GitHubRelease | None:
        return _release("v1.0.0-rc.1")

    checker = UpdateChecker(current_version="1.0.0", fetch_latest=fetcher)
    assert await checker.check() is None


async def test_handles_malformed_version() -> None:
    """Tags inválidos se parsean como (0,0,0) y nunca son 'mayores'."""

    async def fetcher() -> GitHubRelease | None:
        return _release("vNOPE")

    checker = UpdateChecker(current_version="1.0.0", fetch_latest=fetcher)
    assert await checker.check() is None


async def test_handles_versions_of_different_length() -> None:
    """'1.2' vs '1.2.0' → equivalentes (no hay nueva versión)."""

    async def fetcher() -> GitHubRelease | None:
        return _release("v1.2")

    checker = UpdateChecker(current_version="1.2.0", fetch_latest=fetcher)
    assert await checker.check() is None


async def test_handles_versions_of_different_length_with_real_diff() -> None:
    """'1.2.1' > '1.2' (interpretado como 1.2.0)."""

    async def fetcher() -> GitHubRelease | None:
        return _release("v1.2.1")

    checker = UpdateChecker(current_version="1.2", fetch_latest=fetcher)
    result = await checker.check()
    assert result is not None
