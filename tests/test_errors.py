from kie_avatar_studio.domain.errors import (
    JobValidationError,
    KieClientError,
    KieError,
    KieServerError,
    KieTimeoutError,
)


def test_kie_error_hierarchy() -> None:
    assert issubclass(KieClientError, KieError)
    assert issubclass(KieServerError, KieError)
    assert issubclass(KieTimeoutError, KieError)
    assert not issubclass(JobValidationError, KieError)
    assert issubclass(JobValidationError, ValueError)
