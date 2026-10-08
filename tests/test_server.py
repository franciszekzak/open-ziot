"""Exercise a configured receiver with a synthetic stock-protocol camera."""

import base64, functools, hashlib, io, json, operator, socket, struct, subprocess, sys, tempfile, time, unittest
from pathlib import Path
import urllib.request, urllib.error
from http.client import HTTPConnection
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

UID = b"121212121212"


def cloud(command, sequence, body=b""):
    value = (
        b"\x86\x86"
        + struct.pack(">HHHH", command, len(UID) + len(body), sequence, len(UID))
        + UID
        + body
    )
    return value + bytes([functools.reduce(operator.xor, value, 0), 13])


def jpeg_rtp():
    output = io.BytesIO()
    Image.new("RGB", (64, 48), "purple").save(output, "JPEG", quality=75, subsampling=2)
    jpeg = output.getvalue()
    cursor = 2
    tables = {}
    while cursor < len(jpeg):
        marker = jpeg[cursor + 1]
        length = struct.unpack_from(">H", jpeg, cursor + 2)[0]
        body = jpeg[cursor + 4 : cursor + 2 + length]
        if marker == 0xDB:
            for offset in range(0, len(body), 65):
                tables[body[offset]] = body[offset + 1 : offset + 65]
        if marker == 0xDA:
            entropy = jpeg[cursor + 2 + length : -2]
            break
        cursor += 2 + length
    for index, offset in enumerate(range(0, len(entropy), 1000)):
        chunk = entropy[offset : offset + 1000]
        last = offset + len(chunk) == len(entropy)
        header = bytes([0]) + offset.to_bytes(3, "big") + bytes([1, 255, 8, 6])
        if offset == 0:
            header += struct.pack(">BBH", 0, 0, 128) + tables[0] + tables[1]
        yield (
            struct.pack(">BBHII", 128, 26 | (128 if last else 0), index, 90000, int(UID[-8:], 16))
            + header
            + chunk
        )


def port(kind):
    with socket.socket(socket.AF_INET, kind) as value:
        value.bind(("127.0.0.1", 0))
        return value.getsockname()[1]


class ReceiverTest(unittest.TestCase):
    def test_configured_receiver(self):
        with tempfile.TemporaryDirectory(prefix="open-ziot-server-test-") as directory:
            root = Path(directory)
            http = port(socket.SOCK_STREAM)
            tcp = port(socket.SOCK_STREAM)
            rtp = port(socket.SOCK_DGRAM)
            stun = port(socket.SOCK_DGRAM)
            origin = "http://127.0.0.1:" + str(http)
            config = {
                "uid": UID.decode(),
                "public_ip": "127.0.0.1",
                "public_url": origin + "/",
                "username": "test",
                "password_sha256": hashlib.sha256(b"test-password").hexdigest(),
                "http_port": http,
                "signaling_port": tcp,
                "rtp_port": rtp,
                "stun_port": stun,
                "data_dir": str(root / "data"),
            }
            path = root / "config.json"
            path.write_text(json.dumps(config))
            with (root / "log.txt").open("w") as log:
                process = subprocess.Popen(
                    [sys.executable, str(ROOT / "server/camera_service.py"), "--config", str(path)],
                    stdout=log,
                    stderr=log,
                )
                try:
                    auth = "Basic " + base64.b64encode(b"test:test-password").decode()

                    def get(path, authenticated=True):
                        return urllib.request.urlopen(
                            urllib.request.Request(
                                origin + path,
                                headers={"Authorization": auth} if authenticated else {},
                            ),
                            timeout=3,
                        )

                    for _ in range(50):
                        try:
                            with get("/status.json") as response:
                                json.load(response)
                            break
                        except OSError:
                            if process.poll() is not None:
                                raise AssertionError((root / "log.txt").read_text())
                            time.sleep(0.1)
                    else:
                        raise AssertionError("HTTP receiver did not start")
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        get("/audio.json", False)
                    self.assertEqual(error.exception.code, 401)
                    self.assertEqual(error.exception.headers["Content-Length"], "0")
                    self.assertEqual(error.exception.headers["Cache-Control"], "no-store")
                    self.assertEqual(error.exception.read(), b"")
                    with get("/login", False) as response:
                        self.assertIn(b'autocomplete="current-password"', response.read())

                    # Exercise the mobile form without HTTP Basic credentials.
                    connection = HTTPConnection("127.0.0.1", http, timeout=3)

                    def login(body, request_origin=origin):
                        connection.request(
                            "POST",
                            "/login",
                            body,
                            {
                                "Content-Type": "application/x-www-form-urlencoded",
                                "Origin": request_origin,
                            },
                        )
                        response = connection.getresponse()
                        response.read()
                        return response

                    self.assertEqual(login("username=test&password=wrong").status, 403)
                    self.assertEqual(
                        login(
                            "username=test&password=test-password", "https://wrong.example"
                        ).status,
                        403,
                    )
                    response = login("username=test&password=test-password")
                    self.assertEqual(response.status, 303)
                    self.assertEqual(response.getheader("Location"), "/")
                    cookie = response.getheader("Set-Cookie")
                    self.assertIn("HttpOnly", cookie)
                    self.assertIn("SameSite=Strict", cookie)
                    session = cookie.split(";", 1)[0]
                    for target in ("/", "/recording.json", "/files.json"):
                        connection.request("GET", target, headers={"Cookie": session})
                        response = connection.getresponse()
                        self.assertEqual(response.status, 200)
                        response.read()
                    connection.request("GET", "/", headers={"Cookie": session + "tampered"})
                    response = connection.getresponse()
                    self.assertEqual(response.status, 401)
                    response.read()
                    connection.close()
                    with socket.create_connection(("127.0.0.1", tcp), timeout=3) as signaling:
                        signaling.sendall(cloud(1, 1, b"test-firmware\0"))
                        raw = signaling.recv(4096)
                        body = raw[10 + len(UID) : -2]
                        self.assertEqual(struct.unpack_from(">H", raw, 2)[0], 0x8000)
                        self.assertEqual(struct.unpack_from(">H", body, 24)[0], rtp)
                        self.assertEqual(struct.unpack_from(">H", body, 58)[0], stun)
                        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                            for packet in jpeg_rtp():
                                sender.sendto(packet, ("127.0.0.1", rtp))
                            sender.sendto(
                                struct.pack(">BBHII", 128, 0, 1, 256, int(UID[-8:], 16))
                                + bytes([128]) * 256,
                                ("127.0.0.1", rtp),
                            )
                        time.sleep(0.1)
                        with get("/snapshot.jpg") as response:
                            picture = Image.open(io.BytesIO(response.read()))
                            picture.load()
                            self.assertEqual(picture.size, (64, 48))
                        with get("/audio.json") as response:
                            self.assertTrue(json.load(response)["packets"])

                        def post(headers):
                            return urllib.request.urlopen(
                                urllib.request.Request(
                                    origin + "/control",
                                    data=b'{"action":"settings","settings":{"motion":true}}',
                                    headers={
                                        "Authorization": auth,
                                        "Content-Type": "application/json",
                                        "X-Camera-Control": "1",
                                        **headers,
                                    },
                                ),
                                timeout=3,
                            )

                        with self.assertRaises(urllib.error.HTTPError) as error:
                            post({"Origin": "https://wrong.example"})
                        self.assertEqual(error.exception.code, 403)
                        with post({"Origin": origin}) as response:
                            self.assertTrue(json.load(response)["settings"]["motion"])
                        # The camera normally keeps signaling open indefinitely.
                        process.terminate()
                        process.wait(timeout=5)
                finally:
                    if process.poll() is None:
                        process.terminate()
                    process.wait(timeout=10)
                    self.assertEqual(process.returncode, 0, (root / "log.txt").read_text())


if __name__ == "__main__":
    unittest.main()
