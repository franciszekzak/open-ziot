"""Configurable stock X5 receiver and authenticated browser viewer."""

import argparse, ipaddress, os, asyncio, base64, functools, hashlib, hmac, json, operator, re, signal, socket, struct, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from collections import deque
from urllib.parse import urlsplit, parse_qs
from rtp_jpeg import JpegReceiver
from camera_recorder import Recorder

BASE = Path(__file__).resolve().parent
parser = argparse.ArgumentParser(
    description="Self-hosted receiver for compatible stock Z-IoT cameras"
)
parser.add_argument(
    "--config", type=Path, default=Path(os.environ.get("ZIOT_CONFIG", "config.json"))
)
args = parser.parse_args()
CONFIG = json.loads(args.config.read_text())
if not re.fullmatch(r"[0-9A-Fa-f]{8,32}", CONFIG["uid"]):
    raise ValueError("uid must be 8-32 hexadecimal characters")
ipaddress.IPv4Address(CONFIG["public_ip"])
for key, default in [
    ("signaling_port", 18882),
    ("stun_port", 18883),
    ("rtp_port", 18884),
    ("http_port", 18886),
]:
    CONFIG.setdefault(key, default)
    if type(CONFIG[key]) is not int or not 1 <= CONFIG[key] <= 65535:
        raise ValueError("Invalid " + key)
if CONFIG["stun_port"] == CONFIG["rtp_port"]:
    raise ValueError("STUN and RTP ports must differ")
url = urlsplit(CONFIG["public_url"])
if url.scheme not in ("http", "https") or not url.netloc or url.username or url.password:
    raise ValueError("Invalid public_url")
ORIGIN = url.scheme + "://" + url.netloc
if len(CONFIG["public_ip"].encode()) > 20:
    raise ValueError("Invalid advertised IP")
if not re.fullmatch("[0-9a-f]{64}", CONFIG["password_sha256"]):
    raise ValueError("Invalid password hash")
CAPACITY = int(CONFIG.get("storage_limit_gib", 2) * 1024**3)
if CAPACITY < 100 * 1024**2:
    raise ValueError("Storage quota must be at least 100 MiB")
DATA = Path(CONFIG.get("data_dir", "data")).resolve()
DATA.mkdir(parents=True, exist_ok=True)
UID = CONFIG["uid"].encode()
PUBLIC = CONFIG["public_ip"].encode()
SSRC = int(UID[-8:].decode(), 16)
state = {
    "jpeg": None,
    "frame_time": 0,
    "frames": 0,
    "connected": False,
    "ip": None,
    "last_contact": 0,
}
audio = deque(maxlen=96)
audio_lock = threading.Lock()
state.update(
    audio_packets=0, audio_time=0, width=None, height=None, quantization=None, properties=None
)
recorder = None


def log(event, **fields):
    print(json.dumps({"time": time.time(), "event": event, **fields}), flush=True)


def packet(cmd, seq, body=b""):
    data = b"\x86\x86" + struct.pack(">HHHH", cmd, len(UID) + len(body), seq, len(UID)) + UID + body
    return data + bytes([functools.reduce(operator.xor, data, 0), 13])


HTML = (BASE / "viewer.html").read_bytes()


class Viewer(BaseHTTPRequestHandler):
    def authenticated(self):
        auth = self.headers.get("Authorization", "")
        try:
            if not auth.startswith("Basic ") or len(auth) > 2048:
                raise ValueError()
            credentials = base64.b64decode(auth[6:], validate=True)
            username, password = credentials.split(b":", 1)
            valid = hmac.compare_digest(
                username, CONFIG["username"].encode()
            ) and hmac.compare_digest(
                hashlib.sha256(password).hexdigest(), CONFIG["password_sha256"]
            )
        except (ValueError, TypeError):
            valid = False
        if not valid:
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="Open ZIOT", charset="UTF-8"')
            self.end_headers()
        return valid

    def do_GET(self):
        if not self.authenticated():
            return
        parsed = urlsplit(self.path)
        path = parsed.path
        if path == "/":
            body = HTML
            kind = "text/html; charset=utf-8"
            code = 200
        elif path == "/status.json":
            body = json.dumps(
                {
                    "connected": state["connected"],
                    "frames": state["frames"],
                    "frame_age_seconds": round(time.time() - state["frame_time"], 2)
                    if state["frame_time"]
                    else None,
                    "width": state["width"],
                    "height": state["height"],
                    "quantization_mean": state["quantization"],
                    "audio_packets": state["audio_packets"],
                    "audio_age_seconds": round(time.time() - state["audio_time"], 2)
                    if state["audio_time"]
                    else None,
                    "properties": state["properties"],
                }
            ).encode()
            kind = "application/json"
            code = 200
        elif path == "/audio.json":
            try:
                after = max(0, int(parse_qs(parsed.query).get("after", ["0"])[0]))
            except ValueError:
                after = 0
            with audio_lock:
                recent = [p for p in audio if time.time() - p["received"] < 1]
                packets = recent[-4:] if after == 0 else [p for p in recent if p["id"] > after]
                body = json.dumps(
                    {
                        "packets": [
                            {k: v for k, v in p.items() if k != "received"} for p in packets
                        ],
                        "latest": state["audio_packets"],
                        "age_seconds": round(time.time() - state["audio_time"], 2)
                        if state["audio_time"]
                        else None,
                    }
                ).encode()
            kind = "application/json"
            code = 200
        elif path == "/snapshot.jpg":
            body = state["jpeg"]
            if body is None or time.time() - state["frame_time"] > 3:
                body = b"Camera offline"
                code = 503
                kind = "text/plain"
            else:
                code = 200
                kind = "image/jpeg"
        elif path == "/recording.json":
            body = json.dumps(recorder.status()).encode()
            kind = "application/json"
            code = 200
        elif path == "/files.json":
            body = json.dumps(recorder.files()).encode()
            kind = "application/json"
            code = 200
        elif path.startswith("/files/"):
            return self.media(path[7:])
        else:
            code = 404
            body = b"Not found"
            kind = "text/plain"
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' blob:; script-src 'unsafe-inline'; style-src 'unsafe-inline'; frame-ancestors 'none'",
        )
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def media(self, name):
        try:
            path = recorder.resolve(name)
            size = path.stat().st_size
        except (ValueError, FileNotFoundError):
            self.send_error(404)
            return
        start = 0
        end = size - 1
        code = 200
        requested = self.headers.get("Range")
        if requested:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", requested)
            if not match or not any(match.groups()):
                self.send_error(416)
                return
            left, right = match.groups()
            if left:
                start = int(left)
                end = min(int(right), end) if right else end
            else:
                start = max(0, size - int(right))
            if start > end or start >= size:
                self.send_error(416)
                return
            code = 206
        self.send_response(code)
        self.send_header("Content-Type", "video/mp4" if path.suffix == ".mp4" else "image/jpeg")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if code == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        try:
            with path.open("rb") as source:
                source.seek(start)
                remaining = end - start + 1
                while remaining:
                    chunk = source.read(min(65536, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError, FileNotFoundError):
            pass

    def do_POST(self):
        if not self.authenticated():
            return
        if (
            self.path != "/control"
            or self.headers.get("X-Camera-Control") != "1"
            or self.headers.get("Origin") != ORIGIN
            or self.headers.get("Content-Type") != "application/json"
        ):
            self.send_error(403)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 4096:
                raise ValueError("Invalid request size")
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError("Invalid request")
            action = data.get("action")
            if action == "start":
                result = recorder.start()
            elif action == "stop":
                result = recorder.stop()
            elif action == "settings":
                result = recorder.configure(data.get("settings", {}))
            elif action == "photo":
                result = recorder.photo()
            elif action == "delete":
                recorder.delete(data.get("name", ""))
                result = {"deleted": True}
            else:
                raise ValueError("Unknown action")
            body = json.dumps(result).encode()
            code = 200
        except (ValueError, TypeError, OSError) as error:
            body = json.dumps({"error": str(error)}).encode()
            code = 400
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class Datagram(asyncio.DatagramProtocol):
    def __init__(self):
        self.receivers = {}

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        if addr[0] != state["ip"] or time.time() - state["last_contact"] > 180:
            return
        if len(data) >= 20 and data[:2] == b"\0\1":
            mapped = (
                struct.pack(">HH", 1, 8)
                + b"\0\1"
                + struct.pack(">H", addr[1])
                + socket.inet_aton(addr[0])
            )
            if data[4:8] == bytes.fromhex("2112a442"):
                xip = bytes(
                    a ^ b for a, b in zip(socket.inet_aton(addr[0]), bytes.fromhex("2112a442"))
                )
                mapped += (
                    struct.pack(">HH", 0x20, 8)
                    + b"\0\1"
                    + struct.pack(">H", addr[1] ^ 0x2112)
                    + xip
                )
            self.transport.sendto(
                struct.pack(">HH", 0x101, len(mapped)) + data[4:20] + mapped, addr
            )
        elif len(data) >= 12 and data[0] >> 6 == 2 and struct.unpack_from(">I", data, 8)[0] == SSRC:
            if data[1] & 127 == 0:
                cursor = 12 + 4 * (data[0] & 15)
                end = len(data)
                if data[0] & 16:
                    if end < cursor + 4:
                        return
                    cursor += 4 + 4 * struct.unpack_from(">H", data, cursor + 2)[0]
                if data[0] & 32:
                    padding = data[-1]
                    if not padding or padding > end - cursor:
                        return
                    end -= padding
                if end <= cursor or end - cursor > 2048:
                    return
                stamp = struct.unpack_from(">I", data, 4)[0]
                with audio_lock:
                    if (
                        audio
                        and time.time() - state["audio_time"] < 2
                        and ((stamp - audio[-1]["timestamp"]) & 0xFFFFFFFF) > 0x7FFFFFFF
                    ):
                        return
                    if audio and stamp == audio[-1]["timestamp"]:
                        return
                    state["audio_packets"] += 1
                    state["audio_time"] = time.time()
                    audio.append(
                        {
                            "id": state["audio_packets"],
                            "timestamp": stamp,
                            "data": base64.b64encode(data[cursor:end]).decode(),
                            "received": state["audio_time"],
                        }
                    )
                if recorder:
                    recorder.on_audio(data[cursor:end])
                return
            if addr not in self.receivers:
                if len(self.receivers) >= 4:
                    self.receivers.pop(next(iter(self.receivers)))
                self.receivers[addr] = JpegReceiver()
            receiver = self.receivers[addr]
            image = receiver.receive(data)
            if image:
                state["jpeg"] = image
                state["frame_time"] = time.time()
                state["frames"] += 1
                state["width"] = receiver.width
                state["height"] = receiver.height
                state["quantization"] = receiver.quantization
                if state["frames"] == 1 or state["frames"] % 500 == 0:
                    log("video", frames=state["frames"], jpeg_bytes=len(image))


async def main():
    global recorder
    recorder = Recorder(DATA / "recordings", state, log, capacity=CAPACITY)
    active = None
    connections = set()

    async def signaling(reader, writer):
        nonlocal active
        connections.add(writer)
        peer = writer.get_extra_info("peername")
        buffer = b""
        seq = 0
        started = False
        maintenance = None

        def send(cmd, body=b""):
            nonlocal seq
            seq = (seq + 1) & 65535
            writer.write(packet(cmd, seq, body))

        async def maintain():
            await asyncio.sleep(5)
            send(0x9003, PUBLIC.ljust(20, b"\0") + struct.pack(">H", CONFIG["rtp_port"]))
            await writer.drain()
            await asyncio.sleep(1)
            send(0x8005)
            await writer.drain()
            log("relay-start")
            last_retry = time.time()
            while True:
                await asyncio.sleep(10)
                # Reference live timeout is 35 seconds; camera keepalives can be delayed.
                send(0x8002)
                await writer.drain()
                if time.time() - state["frame_time"] > 15 and time.time() - last_retry > 30:
                    send(0x8005)
                    await writer.drain()
                    last_retry = time.time()
                    log("relay-retry")

        try:
            while True:
                data = await asyncio.wait_for(reader.read(4096), 90)
                if not data:
                    break
                buffer += data
                while len(buffer) >= 10:
                    if not buffer.startswith(b"\x86\x86"):
                        raise ValueError("Invalid prefix")
                    cmd, length, cseq, uidlen = struct.unpack(">HHHH", buffer[2:10])
                    if length > 2048 or uidlen != len(UID) or length < uidlen:
                        raise ValueError("Invalid length")
                    if len(buffer) < length + 12:
                        break
                    raw, buffer = buffer[: length + 12], buffer[length + 12 :]
                    if (
                        raw[10 : 10 + uidlen] != UID
                        or raw[-1] != 13
                        or functools.reduce(operator.xor, raw[:-2], 0) != raw[-2]
                    ):
                        raise ValueError("Unexpected device/packet")
                    if active is not writer:
                        if cmd != 1:
                            raise ValueError("Login required")
                        if active is not None and not active.is_closing():
                            active.close()
                        active = writer
                        state["ip"] = peer[0]
                        state["connected"] = True
                        with audio_lock:
                            audio.clear()
                        log(
                            "camera-connected",
                            source=peer[0],
                            firmware=raw[10 + uidlen : -2].rstrip(b"\0").decode(errors="replace"),
                        )
                    state["last_contact"] = time.time()
                    if cmd == 4:
                        state["properties"] = raw[10 + uidlen : -2].hex()
                        log("properties", body=state["properties"])
                    if cmd in (0x1301, 0x1201):
                        log("control-response", command=hex(cmd), body=raw[10 + uidlen : -2].hex())
                    if cmd == 1:
                        send(
                            0x8000,
                            struct.pack(">H", cseq)
                            + PUBLIC.ljust(20, b"\0")
                            + b"\0\0"
                            + struct.pack(">H", CONFIG["rtp_port"])
                            + PUBLIC.ljust(32, b"\0")
                            + struct.pack(">H", CONFIG["stun_port"]),
                        )
                    elif cmd == 0x21:
                        send(0x8021, struct.pack(">I", int(time.time())))
                    elif cmd == 2:
                        send(0x8001, struct.pack(">HHB", cseq, cmd, 0))
                        send(0x8002)
                    elif cmd < 0x8000:
                        send(0x8001, struct.pack(">HHB", cseq, cmd, 0))
                    await writer.drain()
                    if cmd in (0x21, 2) and not started:
                        started = True
                        maintenance = asyncio.create_task(maintain())
        except (OSError, TimeoutError, ValueError) as exc:
            log("connection-ended", reason=str(exc))
        finally:
            connections.discard(writer)
            if maintenance:
                maintenance.cancel()
                await asyncio.gather(maintenance, return_exceptions=True)
            if active is writer:
                active = None
                state["connected"] = False
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass

    http = ThreadingHTTPServer((CONFIG.get("http_bind", "127.0.0.1"), CONFIG["http_port"]), Viewer)
    threading.Thread(target=http.serve_forever, daemon=True).start()
    loop = asyncio.get_running_loop()
    transports = []
    for port in (CONFIG["stun_port"], CONFIG["rtp_port"]):
        transport, _ = await loop.create_datagram_endpoint(
            Datagram, local_addr=(CONFIG.get("ingest_bind", "0.0.0.0"), port)
        )
        transports.append(transport)
    server = await asyncio.start_server(
        signaling, CONFIG.get("ingest_bind", "0.0.0.0"), CONFIG["signaling_port"]
    )
    log("listening", viewer_port=CONFIG["http_port"])
    stopping = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stopping.set)
    try:
        async with server:
            await stopping.wait()
            server.close()
            for writer in tuple(connections):
                writer.close()
    finally:
        http.shutdown()
        await asyncio.to_thread(recorder.shutdown)
        for transport in transports:
            transport.close()


if __name__ == "__main__":
    asyncio.run(main())
