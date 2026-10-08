# Observed stock protocol

These are independent implementation notes, not vendor source. Tested hardware
announced model X5 and firmware `TXW817_A_V1.0.11.84`. Reference dumps were used
to understand behavior; they were not flashed and are not distributed here.

## Bluetooth setup

Advertising names follow `ZIOTA_<device ID>`. GATT UUIDs:

- Service: `36000001-0001-0002-0003-010203040506`
- Write with response: `36000002-0001-0002-0003-010203040506`
- Notify: `36000003-0001-0002-0003-010203040506`

Subscribe through the normal CCCD before writing. Split the frame into 20-byte
GATT writes, awaiting each response and allowing 150 ms between chunks.

All multi-byte fields below are big-endian. Frame:

```text
86 86 | u16(body length + 5) | u16(sequence) | u16(body length)
body | u8(XOR of all preceding bytes) | 0D
```

Provisioning body:

```text
u16(command=1)
u8(SSID UTF-8 byte length), SSID bytes                 [max 32]
u8(password UTF-8 byte length), password bytes         [max 64]
u8(endpoint byte length), ASCII hostname:port          [max 128]
u16(timezone offset in minutes)
u32(registration code)
```

The working setup used UTC+120 minutes. Other timezone behavior has not been
hardware-verified; the app encodes a signed offset in the same 16-bit field.
The registration field is populated with a random nonzero 32-bit number. The
receiver does not treat it as cryptographic authentication. Disconnect during
the final write is inconclusive; verify the resulting network connection.

## Cloud signaling

```text
86 86 | u16(command) | u16(UID length + body length)
u16(sequence) | u16(UID length) | UID bytes | body | XOR | 0D
```

Camera login command `1` contains the firmware string. Server `0x8000` reply:

```text
u16(login sequence) | relay IPv4 ASCII padded to 20 bytes
2 reserved zero bytes | u16(relay UDP port)
STUN IPv4 ASCII padded to 32 bytes | u16(STUN UDP port)
```

- `0x8021`: current UTC Unix time as u32, in response to camera `0x21`.
- `0x8001`: sequence u16, acknowledged command u16, result byte 0.
- `0x8002`: empty heartbeat, including unsolicited sends every ten seconds.
- `0x9003`: relay address padded to 20 bytes followed by UDP port u16.
- `0x8005`: empty start-stream request, after setting the relay endpoint.

A reference live timeout was 35 seconds; periodic server heartbeats keep the
verified device streaming. STUN mapped-address replies support the observed
legacy and magic-cookie forms. Video is RFC2435 JPEG/RTP payload 26, with
explicit 128-byte quantization tables. Audio is PCMU/RTP payload 0 at 8 kHz mono.
Observed SSRC is the final eight UID characters interpreted as hexadecimal.

Local RTSP was reachable in the camera's hotspot mode at port 7070 and path
`/webcam/jpeg_dvp`. It was not reachable after joining home Wi-Fi; the receiver
therefore uses the outbound cloud/relay path. Do not assume station-mode RTSP.

## Unsupported commands

Do not send unknown property, OTA, format or reset commands to discover their
meaning. In reference firmware, `0x8301` selector 5 formats an SD card. It was
misidentified during initial private diagnostics; the device had no SD card,
and that command is not part of this project. Selector 22 clarity values 0–2
were tested without obtaining any resolution/compression change. Neither
command is exposed in the reusable receiver.
