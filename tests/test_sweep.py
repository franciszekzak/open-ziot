"""Verify retention order, threshold, sidecars and excluded captures."""

import tempfile
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
from camera_sweep import sweep

with tempfile.TemporaryDirectory(prefix="x5-sweep-test-") as directory:
    root = Path(directory)
    oldest = root / "20261001T000000Z-00000001.mp4"
    newest = root / "20261002T000000Z-00000002.mp4"
    oldest.write_bytes(b"a" * 30)
    oldest.with_suffix(".json").write_bytes(b"{}")
    newest.write_bytes(b"b" * 30)
    newest.with_suffix(".json").write_bytes(b"{}")
    photo = root / "20261001T000000Z-00000003.jpg"
    photo.write_bytes(b"p" * 36)
    result = sweep(root, capacity=100)
    assert result["before_bytes"] == 100 and result["after_bytes"] == 68
    assert [x["name"] for x in result["deleted_videos"]] == [oldest.name]
    assert not oldest.exists() and not oldest.with_suffix(".json").exists()
    assert newest.exists() and photo.exists()
    photo.write_bytes(b"p" * 43)  # exactly 75: leave the remaining video alone
    assert sweep(root, 100)["deleted_videos"] == []
    newest.with_suffix(".pending.json").write_bytes(b"{}")
    result = sweep(root, 100)
    assert result["after_bytes"] == 77 and not result["target_reached"]
    assert newest.exists() and photo.exists()
    print(
        "PASS: oldest first, stop at target, exact threshold no-op, preserve photos and unfinished video"
    )
