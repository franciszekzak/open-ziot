# Open ZIOT

An independent, self-hosted replacement for the setup and cloud receiver used
by compatible **X5 / X5Mini Z-IoT-Cam** Wi-Fi cameras. Original camera firmware
stays installed. A small Android app sends Wi-Fi and server settings over
Bluetooth; your server receives video/audio directly and hosts the viewer.

**Experimental v0.1.0.** Camera/server protocol verified on an X5 announcing
`TXW817_A_V1.0.11.84`. The Android APK builds and its provisioning packet has
automated tests, but phone-to-camera BLE operation still needs device testing.
Similar product names or cases do not guarantee compatible hardware/protocols.

```text
Android app --Bluetooth setup--> camera
camera --home Wi-Fi / outbound connection--> your server --HTTPS--> app or browser
```

No vendor account, always-on home PC, home port forwarding, custom firmware or
SD card is needed. The camera does need continuous USB power and working Wi-Fi.

## Features

- Android BLE discovery and setup: Wi-Fi credentials, signaling host/port and UTC offset.
- HTTPS viewer in the app or a browser; existing username/password authentication.
- Alternative browser login form at `https://your-host/cat-camera/login` if a
  mobile browser stalls at the HTTP Basic login popup (use your configured viewer path).
- Live JPEG video and PCMU microphone playback after tapping **Enable audio**.
- Server-side manual recording, motion/noise triggers and timed JPEG snapshots.
- H.264/AAC MP4 clips; authenticated preview, download, seek and delete.
- Configurable storage quota; daily cleanup removes oldest completed videos
  above 75% usage until at/below 75%, preserving photos and unfinished captures.

Motion uses basic pixel differences, not object recognition. Noise uses an
adjustable RMS dBFS threshold. Recordings are sampled at 5 fps. Automatic clips
last up to five minutes; manual clips up to thirty minutes. The verified live
stream is **640×480**: a working 1080p mode has not been demonstrated.

## Server: Ubuntu installation

Use an Ubuntu 24.04 server with a public IPv4 address and a hostname pointing
to it. One receiver instance serves one configured camera ID. Use separate
instances/ports/data directories for multiple cameras.

```sh
git clone https://github.com/franciszekzak/open-ziot.git
cd open-ziot
sudo bash deploy/install-ubuntu.sh
```

The installer creates a dedicated `openziot` user, installs Ubuntu's ffmpeg
and Pillow packages, and asks for:

1. The camera ID shown in the Android discovery list (suffix of `ZIOTA_…`).
2. The server's public **IPv4 address** for relay/STUN replies.
3. The HTTPS viewer URL, e.g. `https://camera.example.org/cat-camera/`.

It generates a random viewer password and shows it once. Save it privately.
Only its SHA-256 hash is stored. Configuration is `/etc/open-ziot/config.json`;
recordings/settings are under `/var/lib/open-ziot/recordings`.

Set up an HTTPS reverse proxy to `127.0.0.1:18886`, preserving the request's
Authorization header and stripping `/cat-camera/` before proxying. A Caddy
example is in `deploy/Caddyfile.example`. For an existing Apache HTTPS vhost:

```apache
ProxyPass /cat-camera/ http://127.0.0.1:18886/ connectiontimeout=5 timeout=15
ProxyPassReverse /cat-camera/ http://127.0.0.1:18886/
RedirectMatch 302 ^/cat-camera$ /cat-camera/
```

Enable Apache `proxy`/`proxy_http` modules if needed. HTTPS setup depends on your
existing server; the installer does not replace your webserver or firewall.
Allow TCP **18882** and UDP **18883–18884** to the server, plus your HTTPS port.
Keep backend HTTP **18886** on loopback. Changing ports requires matching
firewall/proxy configuration and the Android signaling-port field.

```sh
sudo systemctl status open-ziot
sudo journalctl -u open-ziot -n 30 --no-pager
sudo systemctl list-timers open-ziot-sweep.timer
```

The timer defaults to **03:00 Europe/Warsaw**, runs a missed sweep after server
startup, and can be changed with `sudo systemctl edit open-ziot-sweep.timer`:

```ini
[Timer]
OnCalendar=
OnCalendar=*-*-* 03:00:00 YOUR/IANA_TIMEZONE
```

Run `sudo systemctl daemon-reload` and restart the timer after editing. If photos
and unfinished files alone exceed 75%, cleanup reports that it could not reach
the target; it does not delete them. Disable the timer to opt out of automatic
video deletion.

## Server: Docker alternative (Linux host networking)

```sh
python3 server/init_config.py --output config/config.json --data-dir /data
sudo chown -R 10001:10001 config
docker compose up -d --build receiver
```

Only the small `config` directory created by the wizard needs that ownership.
The receiver runs as UID 10001 and records to a named volume. Configure HTTPS
and firewall ports as above. Host networking avoids Docker rewriting the
camera's UDP source address. This compose setup is intended for Linux servers.

For daily retention, schedule this command on the server (e.g. systemd or cron):

```sh
cd /absolute/path/to/open-ziot && docker compose run --rm sweep
```

The sweep service is on a maintenance profile and does not run continually.
The native installer includes the timer automatically; the Docker alternative
requires adding the daily schedule yourself.

## Android setup

Download the APK from [Releases](https://github.com/franciszekzak/open-ziot/releases).
Install it using Android's normal per-source installation permission. Android
8.0+ is supported; Android 12+ requests Nearby Devices access, older versions
need Location permission/services for BLE discovery.

1. Put the camera into its existing Bluetooth setup mode. Button/LED behavior
   varies by model; use its own instructions. The app sends no factory reset.
2. Tap **Find cameras** and select the matching `ZIOTA_…` device. Configure the
   server with its displayed ID before provisioning.
3. Enter your Wi-Fi name/password, signaling hostname and TCP port (18882 by
   default), and the HTTPS viewer URL. The signaling field takes a host, not a URL.
4. Tap **Save settings to camera**. The camera may disconnect Bluetooth on the
   final write while switching to Wi-Fi. The app reports this as needing verification.
5. Open the viewer and sign in with the server-generated credentials. Confirm
   a fresh picture, then power-cycle the camera to verify saved settings.

Setup changes the camera's saved network/server and may replace its vendor
cloud connection. Avoid reprovisioning a working camera without a reason. Many
variants require 2.4 GHz Wi-Fi. WPA3-only compatibility is not established.

The app saves the Wi-Fi name, server host/port and viewer URL locally. It does
not persist the Wi-Fi password, save HTTP-auth passwords, use analytics or
contact a manufacturer account service. Downloads open the system browser,
which may request the viewer login again. The viewer needs a valid HTTPS
certificate; certificate errors are never bypassed.

## Build and test

Android: Java 17+, Android SDK 35, Gradle wrapper 8.11.1 / AGP 8.9.2:

```sh
cd android
./gradlew testDebugUnitTest lintDebug assembleDebug
```

For a signed release, set `ZIOT_KEYSTORE` and `ZIOT_STORE_PASSWORD`; the key alias
must be `openziot`, then run `./gradlew assembleRelease`. Preserve the signing
key privately so future releases can update an installed APK. Never commit keys.
The included GitHub Actions template builds a debug APK and runs tests; official release APKs are
signed separately. Install a debug build separately from a signed release if
signatures differ.

Server tests use synthetic media and an isolated fake camera, with no physical
camera configuration or vendor cloud calls:

```sh
pip install -r server/requirements.txt
python tests/test_server.py
python tests/test_recorder.py
python tests/test_sweep.py
node tests/test_viewer.cjs
```

ffmpeg/ffprobe are required. The server supports Linux/Python 3.10+; retention
uses POSIX file locking. The CI template runs the server suite and Android build/lint/unit tests.
To enable it in your repository, copy `ci/build.yml` to `.github/workflows/build.yml`
using a GitHub login with permission to write workflows. CI is initially an inactive
template so the initial release can be published without expanding login scopes.

## Security and compatibility

The stock camera-to-server signaling and media have **no verified encryption
or strong device authentication**. The UID and source-IP/SSRC checks are packet
filters, not cryptographic authentication. HTTPS protects the browser/app leg.
Keep this limitation in mind when choosing where to point the camera.

The project implements a limited observed protocol, not every feature of the
original app. No firmware replacement, OTA updater, SD formatting, factory
reset, experimental camera-property writes or vendor login is exposed. It
cannot turn arbitrary Wi-Fi cameras into compatible devices. See
[protocol notes](docs/protocol.md) and [privacy](docs/privacy.md).

MIT licensed. Independent project; not affiliated with the camera/app vendor.
Please report the advertised BLE name, model, firmware version and redacted
logs when testing another variant. Do not post Wi-Fi passwords, viewer passwords
or private camera footage in issues.
