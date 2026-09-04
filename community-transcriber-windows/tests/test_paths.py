from __future__ import annotations

from student_schedule_worker.platform.windows.paths import AppPaths


def test_startup_cleanup_removes_only_owned_uuid_job_directories(tmp_path) -> None:
    paths = AppPaths(
        cache=tmp_path / "cache",
        models=tmp_path / "cache" / "models",
        jobs=tmp_path / "cache" / "jobs",
        logs=tmp_path / "logs",
    )
    paths.create()
    orphan = paths.jobs / "ec58e3d5-f338-48c6-acb8-92838c756c67"
    unrelated = paths.jobs / "keep-me"
    orphan.mkdir()
    unrelated.mkdir()
    (orphan / "source-media.part").write_bytes(b"lecture")
    (unrelated / "note.txt").write_text("not owned by worker", encoding="utf-8")

    paths.cleanup_orphaned_jobs()

    assert not orphan.exists()
    assert unrelated.exists()

