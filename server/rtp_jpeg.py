"""Bounded RFC 2435 JPEG/RTP reassembly for this owner's camera feed."""

from pathlib import Path
import struct

DHT = Path(__file__).with_name("jpeg-huffman.bin").read_bytes()


def segment(marker, body):
    return b"\xff" + bytes([marker]) + struct.pack(">H", len(body) + 2) + body


class JpegReceiver:
    def __init__(self):
        self.timestamp = None
        self.parts = {}
        self.header = None
        self.last = None
        self.frames = 0
        self.dropped = 0
        self.width = None
        self.height = None
        self.quantization = None

    def receive(self, data):
        if len(data) < 20 or data[0] >> 6 != 2 or data[1] & 127 != 26:
            return None
        cursor = 12 + 4 * (data[0] & 15)
        if data[0] & 16:
            if len(data) < cursor + 4:
                return None
            cursor += 4 + 4 * struct.unpack_from(">H", data, cursor + 2)[0]
        end = len(data)
        if data[0] & 32:
            padding = data[-1]
            if not padding or padding > end - cursor:
                return None
            end -= padding
        if end < cursor + 8:
            return None
        stamp = struct.unpack_from(">I", data, 4)[0]
        offset = int.from_bytes(data[cursor + 1 : cursor + 4], "big")
        kind, q, width, height = data[cursor + 4 : cursor + 8]
        width = (width or 256) * 8
        height = (height or 256) * 8
        cursor += 8
        restart = 0
        if kind >= 64:
            if end < cursor + 4:
                return None
            restart = struct.unpack_from(">H", data, cursor)[0]
            cursor += 4
            kind -= 64
        if kind not in (0, 1):
            return None
        if stamp != self.timestamp:
            if self.parts:
                self.dropped += 1
            self.timestamp = stamp
            self.parts = {}
            self.header = None
            self.last = None
        if offset == 0:
            if q < 128 or end < cursor + 4:
                return None
            _, precision, length = struct.unpack_from(">BBH", data, cursor)
            cursor += 4
            if precision or length != 128 or end < cursor + length:
                return None
            tables = data[cursor : cursor + length]
            cursor += length
            self.width = width
            self.height = height
            self.quantization = sum(tables) / len(tables)
            sof = struct.pack(">BHHB", 8, height, width, 3) + bytes(
                [1, 0x22 if kind == 1 else 0x21, 0, 2, 0x11, 1, 3, 0x11, 1]
            )
            self.header = (
                b"\xff\xd8"
                + segment(0xDB, b"\0" + tables[:64])
                + segment(0xDB, b"\1" + tables[64:])
                + segment(0xC0, sof)
                + DHT
                + (segment(0xDD, struct.pack(">H", restart)) if restart else b"")
                + segment(0xDA, bytes([3, 1, 0, 2, 0x11, 3, 0x11, 0, 63, 0]))
            )
        fragment = data[cursor:end]
        if offset + len(fragment) > 512 * 1024 or len(self.parts) > 1024:
            return None
        self.parts[offset] = fragment
        if data[1] & 128:
            self.last = offset + len(fragment)
        if self.header is None or self.last is None:
            return None
        pieces = []
        expected = 0
        for position, fragment in sorted(self.parts.items()):
            if position != expected:
                return None
            pieces.append(fragment)
            expected += len(fragment)
        if expected != self.last:
            return None
        image = self.header + b"".join(pieces) + b"\xff\xd9"
        self.parts = {}
        self.header = None
        self.last = None
        self.frames += 1
        return image


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    receiver = JpegReceiver()
    data = args.capture.read_bytes()
    cursor = 0
    while cursor + 4 <= len(data):
        length = struct.unpack_from(">I", data, cursor)[0]
        cursor += 4
        if cursor + length > len(data):
            break
        image = receiver.receive(data[cursor : cursor + length])
        cursor += length
        if image:
            args.output.write_bytes(image)
            print("Decoded JPEG:", len(image), "bytes")
            break
    else:
        raise SystemExit("No complete JPEG frame")
