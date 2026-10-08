"""Exercise real recording/encoding and event triggers using synthetic media."""

import io, json, subprocess, tempfile, time
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
from PIL import Image
from camera_recorder import Recorder, decode_mulaw

decoded = decode_mulaw(bytes(range(256)))
reference = subprocess.run(
    [
        "ffmpeg",
        "-v",
        "error",
        "-f",
        "mulaw",
        "-ar",
        "8000",
        "-ac",
        "1",
        "-i",
        "pipe:0",
        "-f",
        "s16le",
        "pipe:1",
    ],
    input=bytes(range(256)),
    capture_output=True,
    check=True,
).stdout
assert decoded == reference
print("PCMU decoder matches ffmpeg for all 256 codewords", flush=True)


def picture(value):
    stream = io.BytesIO()
    Image.new("RGB", (640, 480), (value, value, value)).save(stream, format="JPEG")
    return stream.getvalue()


with tempfile.TemporaryDirectory(prefix="x5-recorder-test-") as folder:
    state = {"jpeg": picture(0), "frame_time": time.time()}
    recorder = Recorder(folder, state, lambda event, **fields: print(event, fields, flush=True))

    def feed(seconds, changing=False, loud=False):
        stop = time.time() + seconds
        count = 0
        while time.time() < stop:
            state["frame_time"] = time.time()
            if changing:
                state["jpeg"] = picture(255 if int(count / 5) % 2 else 0)
            for _ in range(3):
                recorder.on_audio(bytes([128 if loud else 255]) * 256)
            time.sleep(0.1)
            count += 1

    recorder.start()
    feed(1.2)
    recorder.stop()
    recorder.configure({"motion": True, "motion_percent": 5, "tail_seconds": 3})
    recorder.cooldown = 0
    feed(2.5, changing=True)
    assert recorder.active and recorder.active["reason"] == "motion", recorder.status()
    recorder.stop()
    recorder.configure({"motion": False, "noise": True})
    recorder.cooldown = 0
    feed(1.2, loud=True)
    assert recorder.active and recorder.active["reason"] == "noise", recorder.status()
    recorder.stop()
    recorder.configure({"noise": False, "interval": True, "interval_seconds": 1})
    feed(1.3)
    recorder.configure({"interval": False})
    recorder.shutdown()
    files = recorder.files()
    videos = [f for f in files if f["name"].endswith(".mp4")]
    photos = [f for f in files if f["name"].endswith(".jpg")]
    assert len(videos) == 3 and len(photos) >= 2, files
    for video in videos:
        info = json.loads(
            subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_streams",
                    "-show_format",
                    "-of",
                    "json",
                    str(Path(folder) / video["name"]),
                ],
                capture_output=True,
                check=True,
            ).stdout
        )
        assert {s["codec_type"] for s in info["streams"]} == {"video", "audio"}, info
        assert float(info["format"]["duration"]) >= 0.8
    try:
        recorder.resolve("../config.json")
        raise AssertionError("Traversal accepted")
    except ValueError:
        pass
    try:
        recorder.configure({"interval_seconds": 0})
        raise AssertionError("Invalid interval accepted")
    except ValueError:
        pass
    print("Manual, motion, noise, timed photos, MP4 video+audio and bounds passed", flush=True)
