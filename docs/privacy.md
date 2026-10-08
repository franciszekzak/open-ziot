# Privacy

Open ZIOT has no analytics, advertising, vendor login or third-party camera
cloud integration. The Android app uses Bluetooth to send the network and
server settings you enter to the device you select. The camera must store
those settings to reconnect after power loss.

The app stores non-password setup fields in its private preferences. Wi-Fi
password fields are excluded from view-state saving, and Wi-Fi passwords and
viewer HTTP-auth credentials are not persisted by this application. Android
backups are disabled. Viewer requests go to the HTTPS server URL you provide.
Downloading a saved file opens that URL in your system browser.

The server stores footage and snapshots only when requested or when enabled
triggers fire. It retains recording preferences, timestamps, event reasons
and operational logs such as the device's source IP and firmware. You control
the server, access password, storage quota and automatic retention timer.
Completed videos may be permanently deleted by the configured daily sweep;
photos and unfinished captures are excluded. Disable the timer if you need
to preserve every video.

Camera transport is the stock protocol and is not verified to be encrypted.
HTTPS protects the app/browser connection to your server. People deploying
the project are responsible for their own server operation and privacy notice.
