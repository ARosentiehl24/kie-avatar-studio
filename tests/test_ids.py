from kie_avatar_studio.app_layer.ids import new_job_id, sanitize_filename


def test_new_job_id_format() -> None:
    jid = new_job_id()
    assert jid.startswith("job_")
    parts = jid.split("_")
    assert len(parts) == 4
    assert len(parts[3]) == 6  # uuid short


def test_new_job_id_is_unique() -> None:
    assert new_job_id() != new_job_id()


def test_sanitize_filename() -> None:
    assert sanitize_filename("hola mundo/ñ*.png") == "hola_mundo_.png"
    assert sanitize_filename("///") == "unnamed"
    assert sanitize_filename("") == "unnamed"
    assert sanitize_filename("ok_name-1.mp4") == "ok_name-1.mp4"
