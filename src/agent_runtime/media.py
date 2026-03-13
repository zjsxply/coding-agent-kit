from __future__ import annotations

import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Optional


class MediaStageError(RuntimeError):
    pass


def stage_media_files(
    media_paths: list[Path],
    *,
    staged_media_dirs: set[Path],
    stage_root: Optional[Path] = None,
) -> list[Path]:
    staged: list[Path] = []
    run_stage = f"{os.getpid()}-{time.time_ns()}-{uuid.uuid4().hex[:8]}"
    stage_parent = stage_root if stage_root is not None else Path("/tmp") / "cakit-media"
    stage_dir = stage_parent / run_stage
    stage_dir.mkdir(parents=True, exist_ok=True)
    staged_media_dirs.add(stage_dir)

    for index, media_path in enumerate(media_paths):
        src = media_path.expanduser().resolve()
        if not src.exists() or not src.is_file():
            raise MediaStageError(f"media file not found: {src}")
        suffix = src.suffix
        stem = src.stem or "media"
        safe_stem = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in stem)
        target = stage_dir / f"{index:02d}-{safe_stem}{suffix}"
        try:
            if src != target:
                shutil.copy2(src, target)
            staged.append(target)
        except OSError as exc:
            raise MediaStageError(f"failed to stage media file {src}: {exc}") from exc
    return staged


def cleanup_staged_media(staged_media_dirs: set[Path]) -> None:
    if not staged_media_dirs:
        return
    stage_dirs = tuple(staged_media_dirs)
    staged_media_dirs.clear()
    for stage_dir in stage_dirs:
        shutil.rmtree(stage_dir, ignore_errors=True)
