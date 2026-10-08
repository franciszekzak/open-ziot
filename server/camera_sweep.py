"""Daily retention: remove oldest completed videos above 75% camera capacity."""

import argparse, fcntl, json, re, time
from pathlib import Path
from camera_recorder import Recorder

VIDEO = re.compile(r"\d{8}T\d{6}Z-[0-9a-f]{8}\.mp4\Z")


def usage(root):
    total = 0
    for path in root.iterdir():
        try:
            if path.is_file() and not path.is_symlink():
                total += path.stat().st_size
        except FileNotFoundError:
            pass
    return total


def sweep(root, capacity=Recorder.LIMIT):
    root = Path(root)
    target = capacity * 3 // 4
    before = usage(root)
    deleted = []
    if before > target:
        for path in sorted(root.iterdir(), key=lambda p: p.name):
            if usage(root) <= target:
                break
            if not VIDEO.fullmatch(path.name) or path.is_symlink():
                continue
            identity = path.stem
            # Encoding/recovery source files indicate an unfinished capture.
            if any(
                (root / (identity + suffix)).exists()
                for suffix in (".pending.json", ".mjpeg", ".s16le", ".part.mp4")
            ):
                continue
            try:
                size = path.stat().st_size
                path.unlink()
            except FileNotFoundError:
                continue
            metadata = path.with_suffix(".json")
            if not metadata.is_symlink():
                metadata.unlink(missing_ok=True)
            deleted.append({"name": path.name, "bytes": size})
    after = usage(root)
    return {
        "time": time.time(),
        "event": "daily-sweep",
        "before_bytes": before,
        "after_bytes": after,
        "target_bytes": target,
        "deleted_videos": deleted,
        "target_reached": after <= target,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    config = json.loads(parser.parse_args().config.read_text())
    root = Path(config.get("data_dir", "data")).resolve() / "recordings"
    capacity = int(config.get("storage_limit_gib", 2) * 1024**3)
    with (root / ".sweep.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        print(json.dumps(sweep(root, capacity)), flush=True)
