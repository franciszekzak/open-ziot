"""Server recording, bounded storage, and simple motion/noise triggers."""

import io, json, math, re, shutil, struct, subprocess, threading, time, uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from PIL import Image, ImageChops


def decode_mulaw(data):
    values = []
    for byte in data:
        value = (~byte) & 255
        magnitude = ((value & 15) * 8 + 132) << ((value >> 4) & 7)
        values.append(132 - magnitude if value & 128 else magnitude - 132)
    return struct.pack("<" + "h" * len(values), *values)


class Recorder:
    LIMIT = 2 * 1024**3
    DEFAULTS = {
        "motion": False,
        "motion_percent": 10,
        "noise": False,
        "noise_db": -35,
        "interval": False,
        "interval_seconds": 30,
        "tail_seconds": 10,
    }

    def __init__(self, root, state, logger, capacity=None):
        self.LIMIT = capacity if capacity is not None else type(self).LIMIT
        self.root = Path(root)
        self.root.mkdir(mode=0o700, exist_ok=True)
        self.state = state
        self.log = logger
        self.lock = threading.RLock()
        self.settings = self.DEFAULTS.copy()
        self.settings_file = self.root / "settings.json"
        if self.settings_file.exists():
            self.settings.update(json.loads(self.settings_file.read_text()))
        self.active = None
        self.error = None
        self.motion_score = 0
        self.noise_db = -96
        self.previous = None
        self.motion_hits = 0
        self.noise_hits = 0
        self.last_trigger = 0
        self.last_photo = 0
        self.last_motion = 0
        self.cooldown = 0
        self.pending = 0
        self.running = True
        self.encoder = ThreadPoolExecutor(max_workers=1)
        self.usage = self.disk_usage()
        self.next_usage = 0
        # Recover unfinished captures after a service interruption.
        for meta in self.root.glob("*.pending.json"):
            try:
                capture = json.loads(meta.read_text())
                if self.pending < 3 and (self.root / (capture["id"] + ".mjpeg")).exists():
                    self.pending += 1
                    self.encoder.submit(self.finalize, capture)
            except (ValueError, OSError):
                pass
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def disk_usage(self):
        total = 0
        for path in self.root.iterdir():
            try:
                if path.is_file():
                    total += path.stat().st_size
            except FileNotFoundError:
                pass
        return total

    def status(self):
        with self.lock:
            return {
                "settings": self.settings.copy(),
                "recording": None
                if not self.active
                else {
                    "id": self.active["id"],
                    "reason": self.active["reason"],
                    "seconds": round(time.time() - self.active["start"], 1),
                },
                "encoding": self.pending,
                "motion_percent": round(self.motion_score, 1),
                "noise_db": round(self.noise_db, 1),
                "storage_bytes": self.usage,
                "storage_limit_bytes": self.LIMIT,
                "error": self.error,
            }

    def configure(self, data):
        if set(data) - set(self.DEFAULTS):
            raise ValueError("Unknown setting")
        settings = self.settings.copy()
        settings.update(data)
        for key in ("motion", "noise", "interval"):
            if type(settings[key]) is not bool:
                raise ValueError("Invalid toggle")
        for key, low, high in [
            ("motion_percent", 1, 100),
            ("noise_db", -80, -5),
            ("interval_seconds", 1, 86400),
            ("tail_seconds", 3, 60),
        ]:
            value = settings[key]
            if (
                type(value) not in (int, float)
                or not math.isfinite(value)
                or not low <= value <= high
            ):
                raise ValueError("Invalid " + key)
        with self.lock:
            self.settings = settings
            temporary = self.settings_file.with_suffix(".tmp")
            temporary.write_text(json.dumps(settings))
            temporary.replace(self.settings_file)
        return self.status()

    def start(self, reason="manual"):
        with self.lock:
            if self.active:
                if reason == "manual":
                    self.active["reason"] = "manual"
                return self.status()
            if time.time() - self.state["frame_time"] > 3:
                raise ValueError("Camera offline")
            if self.pending >= 3:
                raise ValueError("Previous clips are still processing")
            self.usage = self.disk_usage()
            if self.usage >= self.LIMIT or shutil.disk_usage(self.root).free < 512 * 1024**2:
                raise ValueError("Storage full; delete saved files to continue")
            now = time.time()
            identity = (
                datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
            )
            capture = {
                "id": identity,
                "start": now,
                "reason": reason,
                "frames": 0,
                "pcm": bytearray(),
            }
            capture["video"] = (self.root / (identity + ".mjpeg")).open("wb")
            capture["audio"] = (self.root / (identity + ".s16le")).open("wb")
            self.active = capture
            self.last_trigger = now
            self.error = None
            (self.root / (identity + ".pending.json")).write_text(
                json.dumps({k: v for k, v in capture.items() if k not in ("video", "audio", "pcm")})
            )
            self.log("recording-start", id=identity, reason=reason)
            return self.status()

    def stop(self):
        with self.lock:
            if not self.active:
                return self.status()
            capture = self.active
            self.active = None
            capture["video"].close()
            capture["audio"].close()
            meta = {k: v for k, v in capture.items() if k not in ("video", "audio", "pcm")}
            meta["duration_seconds"] = capture["frames"] / 5
            (self.root / (capture["id"] + ".pending.json")).write_text(json.dumps(meta))
            self.pending += 1
            self.encoder.submit(self.finalize, meta)
            self.cooldown = time.time() + 3
            return self.status()

    def finalize(self, capture):
        identity = capture["id"]
        video = self.root / (identity + ".mjpeg")
        audio = self.root / (identity + ".s16le")
        output = self.root / (identity + ".mp4")
        temporary = self.root / (identity + ".part.mp4")
        try:
            if not video.exists() or video.stat().st_size == 0:
                raise ValueError("No video frames were captured")
            command = [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-threads",
                "1",
                "-fflags",
                "+genpts",
                "-f",
                "mjpeg",
                "-framerate",
                "5",
                "-i",
                str(video),
            ]
            if audio.exists() and audio.stat().st_size:
                command += [
                    "-f",
                    "s16le",
                    "-ar",
                    "8000",
                    "-ac",
                    "1",
                    "-i",
                    str(audio),
                    "-map",
                    "0:v:0",
                    "-map",
                    "1:a:0",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "32k",
                ]
            command += [
                "-c:v",
                "libx264",
                "-threads",
                "1",
                "-preset",
                "veryfast",
                "-crf",
                "23",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                "-shortest",
                str(temporary),
            ]
            subprocess.run(
                command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=300
            )
            temporary.replace(output)
            if not capture.get("duration_seconds"):
                result = subprocess.run(
                    [
                        "ffprobe",
                        "-v",
                        "error",
                        "-show_entries",
                        "format=duration",
                        "-of",
                        "json",
                        str(output),
                    ],
                    capture_output=True,
                    check=True,
                )
                capture["duration_seconds"] = float(json.loads(result.stdout)["format"]["duration"])
            (self.root / (identity + ".json")).write_text(json.dumps(capture))
            for path in (video, audio, self.root / (identity + ".pending.json")):
                path.unlink(missing_ok=True)
            self.log("recording-saved", id=identity, bytes=output.stat().st_size)
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            self.error = "Recording could not be processed; captured source files were preserved"
            self.log("recording-error", id=identity, reason=str(error))
        finally:
            with self.lock:
                self.pending -= 1
                self.usage = self.disk_usage()

    def on_audio(self, payload):
        pcm = decode_mulaw(payload)
        values = struct.unpack("<" + "h" * (len(pcm) // 2), pcm)
        rms = math.sqrt(sum(x * x for x in values) / max(1, len(values))) / 32768
        with self.lock:
            self.noise_db = max(-96, 20 * math.log10(max(rms, 1e-8)))
            self.noise_hits = (
                self.noise_hits + 1 if self.noise_db >= self.settings["noise_db"] else 0
            )
            if self.settings["noise"] and self.noise_hits >= 3:
                self.trigger("noise")
            if self.active:
                self.active["pcm"].extend(pcm)
                if len(self.active["pcm"]) > 32000:
                    del self.active["pcm"][:-32000]

    def trigger(self, reason):
        self.last_trigger = time.time()
        if not self.active and time.time() >= self.cooldown:
            try:
                self.start(reason)
            except ValueError as error:
                self.error = str(error)
                self.cooldown = time.time() + 10

    def photo(self, reason="snapshot"):
        with self.lock:
            if time.time() - self.state["frame_time"] > 3:
                raise ValueError("Camera offline")
            if self.disk_usage() >= self.LIMIT or shutil.disk_usage(self.root).free < 512 * 1024**2:
                raise ValueError("Storage full")
            identity = (
                datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
            )
            (self.root / (identity + ".jpg")).write_bytes(self.state["jpeg"])
            (self.root / (identity + ".json")).write_text(
                json.dumps({"id": identity, "start": time.time(), "reason": reason})
            )
            self.usage = self.disk_usage()
            return {"name": identity + ".jpg"}

    def files(self):
        result = []
        for path in sorted(self.root.iterdir(), key=lambda p: p.name, reverse=True):
            if path.suffix not in (".jpg", ".mp4") or ".part." in path.name:
                continue
            try:
                info = {"name": path.name, "bytes": path.stat().st_size}
            except FileNotFoundError:
                continue
            try:
                info.update(json.loads(path.with_suffix(".json").read_text()))
            except (ValueError, OSError):
                pass
            result.append(info)
            if len(result) >= 200:
                break
        return result

    def resolve(self, name):
        if not re.fullmatch(r"\d{8}T\d{6}Z-[0-9a-f]{8}\.(mp4|jpg)", name):
            raise ValueError("Invalid file")
        path = self.root / name
        if not path.is_file():
            raise ValueError("File not found")
        return path

    def delete(self, name):
        with self.lock:
            path = self.resolve(name)
            path.unlink()
            path.with_suffix(".json").unlink(missing_ok=True)
            self.usage = self.disk_usage()

    def run(self):
        deadline = time.monotonic()
        while self.running:
            try:
                with self.lock:
                    now = time.time()
                    fresh = now - self.state["frame_time"] < 3
                    picture = self.state["jpeg"]
                    if fresh and self.settings["motion"] and now - self.last_motion >= 0.5:
                        self.last_motion = now
                        current = Image.open(io.BytesIO(picture)).convert("L").resize((32, 24))
                        if self.previous is not None:
                            diff = ImageChops.difference(current, self.previous)
                            self.motion_score = (
                                100 * sum(v > 20 for v in diff.getdata()) / (32 * 24)
                            )
                            self.motion_hits = (
                                self.motion_hits + 1
                                if self.motion_score >= self.settings["motion_percent"]
                                else 0
                            )
                            if self.motion_hits >= 2:
                                self.trigger("motion")
                        self.previous = current
                    elif not self.settings["motion"]:
                        self.previous = None
                        self.motion_score = 0
                        self.motion_hits = 0
                    if (
                        fresh
                        and self.settings["interval"]
                        and now - self.last_photo >= self.settings["interval_seconds"]
                    ):
                        self.last_photo = now
                        try:
                            self.photo("interval")
                        except ValueError as error:
                            self.error = str(error)
                    if self.active:
                        capture = self.active
                        if fresh:
                            capture["video"].write(picture)
                            capture["frames"] += 1
                            samples = bytes(capture["pcm"][:3200])
                            del capture["pcm"][:3200]
                            capture["audio"].write(samples.ljust(3200, b"\0"))
                        if (
                            not fresh
                            or now - capture["start"] > 1800
                            or (
                                capture["reason"] != "manual"
                                and (
                                    now - self.last_trigger > self.settings["tail_seconds"]
                                    or now - capture["start"] > 300
                                )
                            )
                        ):
                            self.stop()
                    if now >= self.next_usage:
                        self.next_usage = now + 5
                        self.usage = self.disk_usage()
                        if self.active and (
                            self.usage >= self.LIMIT
                            or shutil.disk_usage(self.root).free < 512 * 1024**2
                        ):
                            self.error = "Storage full; recording stopped"
                            self.stop()
            except Exception as error:
                self.error = "Capture processing error"
                self.log("capture-error", reason=str(error))
            deadline += 0.2
            if deadline < time.monotonic() - 0.2:
                deadline = time.monotonic()
            time.sleep(max(0, deadline - time.monotonic()))

    def shutdown(self):
        self.running = False
        self.thread.join(timeout=2)
        self.stop()
        self.encoder.shutdown(wait=True)
