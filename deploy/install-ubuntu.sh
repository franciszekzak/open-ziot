#!/bin/bash
set -euo pipefail
if [ "$EUID" -ne 0 ]; then echo 'Run with sudo from the repository root.' >&2; exit 1; fi
project=$(cd "$(dirname "$0")/.." && pwd)
apt-get update -qq
apt-get install -y ffmpeg python3-pil
if ! id openziot >/dev/null 2>&1; then useradd --system --home-dir /var/lib/open-ziot --shell /usr/sbin/nologin openziot; fi
install -d -m 755 /opt/open-ziot
install -d -m 700 -o openziot -g openziot /var/lib/open-ziot /etc/open-ziot
for source in "$project"/server/*.py "$project"/server/*.html "$project"/server/*.bin; do install -m 644 "$source" /opt/open-ziot/; done
if [ ! -f /etc/open-ziot/config.json ]; then
  runuser -u openziot -- python3 /opt/open-ziot/init_config.py --output /etc/open-ziot/config.json --data-dir /var/lib/open-ziot
fi
for name in open-ziot.service open-ziot-sweep.service open-ziot-sweep.timer; do install -m 644 "$project/deploy/$name" /etc/systemd/system/; done
systemctl daemon-reload
systemctl enable --now open-ziot.service open-ziot-sweep.timer
echo 'Receiver installed. Set up HTTPS and firewall ports as described in README.md before provisioning the camera.'
