package io.github.franciszekzak.openziot;

import android.Manifest;
import android.app.Activity;
import android.bluetooth.*;
import android.bluetooth.le.*;
import android.content.*;
import android.content.pm.PackageManager;
import android.os.*;
import android.text.InputType;
import android.view.*;
import android.widget.*;
import java.security.SecureRandom;
import java.util.*;

public class MainActivity extends Activity {
  private static final UUID SERVICE = UUID.fromString("36000001-0001-0002-0003-010203040506");
  private static final UUID WRITE = UUID.fromString("36000002-0001-0002-0003-010203040506");
  private static final UUID NOTIFY = UUID.fromString("36000003-0001-0002-0003-010203040506");
  private static final UUID CCCD = UUID.fromString("00002902-0000-1000-8000-00805f9b34fb");
  private final Handler handler = new Handler(Looper.getMainLooper());
  private final Map<String, BluetoothDevice> found = new LinkedHashMap<>();
  private BluetoothAdapter adapter;
  private BluetoothLeScanner scanner;
  private BluetoothGatt gatt;
  private BluetoothGattCharacteristic write;
  private BluetoothDevice selected;
  private LinearLayout devices;
  private EditText ssid, password, host, port, viewer, timezone;
  private TextView status, selection;
  private Button provision, scan;
  private byte[] packet;
  private int cursor = 0;
  private boolean submitted = false, busy = false;
  private long generation = 0;

  @Override
  public void onCreate(Bundle saved) {
    super.onCreate(saved);
    adapter = ((BluetoothManager) getSystemService(BLUETOOTH_SERVICE)).getAdapter();
    ScrollView scroll = new ScrollView(this);
    LinearLayout form = new LinearLayout(this);
    form.setOrientation(LinearLayout.VERTICAL);
    form.setPadding(32, 40, 32, 40);
    scroll.addView(form);
    TextView title = new TextView(this);
    title.setText("Open ZIOT");
    title.setTextSize(28);
    form.addView(title);
    TextView intro = new TextView(this);
    intro.setText(
        "Connect a compatible X5 camera to your own server. Put the camera in setup mode so it"
            + " advertises ZIOTA_… over Bluetooth. Setup changes its saved Wi-Fi and server.");
    form.addView(intro);
    scan = button(form, "Find cameras", v -> discover());
    devices = new LinearLayout(this);
    devices.setOrientation(LinearLayout.VERTICAL);
    form.addView(devices);
    selection = new TextView(this);
    selection.setText("No camera selected");
    form.addView(selection);
    SharedPreferences prefs = getPreferences(MODE_PRIVATE);
    ssid = field(form, "Home Wi-Fi name (usually 2.4 GHz)", prefs.getString("ssid", ""), false);
    password = field(form, "Wi-Fi password — never saved on this phone", "", true);
    // Keep Wi-Fi secrets out of Android view-state persistence as well.
    password.setSaveEnabled(false);
    host = field(form, "Signaling server hostname / IPv4", prefs.getString("host", ""), false);
    port = field(form, "Signaling TCP port", prefs.getString("port", "18882"), false);
    port.setInputType(InputType.TYPE_CLASS_NUMBER);
    viewer = field(form, "HTTPS viewer URL", prefs.getString("viewer", ""), false);
    timezone =
        field(
            form,
            "UTC offset in minutes",
            String.valueOf(TimeZone.getDefault().getOffset(System.currentTimeMillis()) / 60000),
            false);
    timezone.setInputType(InputType.TYPE_CLASS_NUMBER | InputType.TYPE_NUMBER_FLAG_SIGNED);
    provision = button(form, "Save settings to camera", v -> configure());
    button(
        form,
        "Open live viewer",
        v -> {
          try {
            String url = viewer.getText().toString().trim();
            ViewerActivity.validate(url);
            savePreferences();
            startActivity(new Intent(this, ViewerActivity.class).putExtra("url", url));
          } catch (Exception error) {
            message(error.getMessage());
          }
        });
    status = new TextView(this);
    status.setPadding(0, 24, 0, 0);
    form.addView(status);
    message(
        "The server must be installed and configured with the selected camera ID before setup."
            + " Compatibility verified on X5 stock TXW817_A_V1.0.11.84; other variants need"
            + " testing.");
    scroll.setOnApplyWindowInsetsListener(
        (view, insets) -> {
          view.setPadding(
              0, insets.getSystemWindowInsetTop(), 0, insets.getSystemWindowInsetBottom());
          return insets;
        });
    setContentView(scroll);
  }

  private Button button(LinearLayout parent, String label, View.OnClickListener listener) {
    Button b = new Button(this);
    b.setText(label);
    b.setOnClickListener(listener);
    parent.addView(b);
    return b;
  }

  private EditText field(LinearLayout parent, String label, String value, boolean secret) {
    TextView text = new TextView(this);
    text.setText(label);
    parent.addView(text);
    EditText edit = new EditText(this);
    edit.setSingleLine();
    edit.setText(value);
    edit.setInputType(
        secret
            ? InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD
            : InputType.TYPE_CLASS_TEXT);
    parent.addView(edit);
    return edit;
  }

  private void message(String text) {
    runOnUiThread(() -> status.setText(text));
  }

  private void savePreferences() {
    getPreferences(MODE_PRIVATE)
        .edit()
        .putString("ssid", ssid.getText().toString())
        .putString("host", host.getText().toString().trim())
        .putString("port", port.getText().toString())
        .putString("viewer", viewer.getText().toString().trim())
        .apply();
  }

  private boolean permissions() {
    String[] needed =
        Build.VERSION.SDK_INT >= 31
            ? new String[] {
              Manifest.permission.BLUETOOTH_SCAN, Manifest.permission.BLUETOOTH_CONNECT
            }
            : new String[] {Manifest.permission.ACCESS_FINE_LOCATION};
    for (String p : needed)
      if (checkSelfPermission(p) != PackageManager.PERMISSION_GRANTED) {
        requestPermissions(needed, 1);
        message(
            "Grant Bluetooth access, then tap Find cameras again. Android 8–11 also needs Location"
                + " services enabled for BLE scanning.");
        return false;
      }
    return true;
  }

  private final ScanCallback scanCallback =
      new ScanCallback() {
        @Override
        public void onScanResult(int type, ScanResult result) {
          if (Build.VERSION.SDK_INT >= 31
              && checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT)
                  != PackageManager.PERMISSION_GRANTED) return;
          String name =
              result.getScanRecord() == null ? null : result.getScanRecord().getDeviceName();
          if (name == null || !name.toUpperCase(Locale.ROOT).startsWith("ZIOT")) return;
          String finalName = name;
          BluetoothDevice device = result.getDevice();
          runOnUiThread(
              () -> {
                if (found.putIfAbsent(device.getAddress(), device) != null) return;
                button(
                    devices,
                    finalName + " · " + device.getAddress(),
                    v -> {
                      selected = device;
                      selection.setText(
                          "Selected: "
                              + finalName
                              + "\nCamera ID: "
                              + finalName.substring(finalName.lastIndexOf('_') + 1)
                              + "\nEnter this ID in the server configuration.");
                    });
              });
        }

        @Override
        public void onScanFailed(int error) {
          message("Bluetooth scan failed (" + error + "). Check Bluetooth and permissions.");
        }
      };

  private void discover() {
    if (busy) {
      message("Setup is already in progress.");
      return;
    }
    if (!permissions()) return;
    if (Build.VERSION.SDK_INT >= 31
        && (checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT)
                != PackageManager.PERMISSION_GRANTED
            || checkSelfPermission(Manifest.permission.BLUETOOTH_SCAN)
                != PackageManager.PERMISSION_GRANTED)) return;
    if (adapter == null) {
      message("This phone has no Bluetooth adapter.");
      return;
    }
    if (!adapter.isEnabled()) {
      startActivity(new Intent(BluetoothAdapter.ACTION_REQUEST_ENABLE));
      return;
    }
    scanner = adapter.getBluetoothLeScanner();
    stopScan();
    found.clear();
    devices.removeAllViews();
    scanner.startScan(
        null,
        new ScanSettings.Builder().setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY).build(),
        scanCallback);
    message("Scanning for 15 seconds… Select your camera from the list.");
    handler.postDelayed(this::stopScan, 15000);
  }

  private void configure() {
    if (busy) return;
    if (!permissions()) return;
    if (selected == null) {
      message("Find and select a camera first.");
      return;
    }
    if (Build.VERSION.SDK_INT >= 31
        && checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT)
            != PackageManager.PERMISSION_GRANTED) return;
    try {
      long registration = new SecureRandom().nextInt() & 0xffffffffL;
      if (registration == 0) registration = 1;
      packet =
          Provisioning.packet(
              ssid.getText().toString(),
              password.getText().toString(),
              host.getText().toString().trim(),
              Integer.parseInt(port.getText().toString()),
              Integer.parseInt(timezone.getText().toString()),
              registration,
              1);
      if (!viewer.getText().toString().trim().isEmpty())
        ViewerActivity.validate(viewer.getText().toString().trim());
      savePreferences();
      stopScan();
      closeConnection();
      cursor = 0;
      submitted = false;
      busy = true;
      provision.setEnabled(false);
      scan.setEnabled(false);
      long session = ++generation;
      message("Connecting to the camera’s setup service…");
      gatt = selected.connectGatt(this, false, callback, BluetoothDevice.TRANSPORT_LE);
      handler.postDelayed(
          () -> {
            if (busy && session == generation)
              finish(
                  "Setup timed out. Check the server for a connection before retrying; do not"
                      + " assume the write failed.");
          },
          45000);
    } catch (Exception error) {
      message(error.getMessage());
    }
  }

  private final BluetoothGattCallback callback =
      new BluetoothGattCallback() {
        @Override
        public void onConnectionStateChange(BluetoothGatt connection, int code, int state) {
          handler.post(
              () -> {
                if (connection != gatt) return;
                if (Build.VERSION.SDK_INT >= 31
                    && checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT)
                        != PackageManager.PERMISSION_GRANTED) {
                  finish("Bluetooth permission was revoked.");
                  return;
                }
                if (state == BluetoothProfile.STATE_CONNECTED
                    && code == BluetoothGatt.GATT_SUCCESS) {
                  message("Checking camera services…");
                  connection.discoverServices();
                } else if (state == BluetoothProfile.STATE_DISCONNECTED) {
                  finish(
                      submitted
                          ? "Settings sent. Camera disconnected to join Wi-Fi. Check the"
                                + " viewer/server to confirm; reconnecting alone is not proof of"
                                + " successful setup."
                          : "Camera disconnected before all settings were submitted. Check its"
                                + " network state before retrying.");
                }
              });
        }

        @Override
        public void onServicesDiscovered(BluetoothGatt connection, int code) {
          handler.post(
              () -> {
                if (connection != gatt) return;
                if (Build.VERSION.SDK_INT >= 31
                    && checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT)
                        != PackageManager.PERMISSION_GRANTED) {
                  finish("Bluetooth permission was revoked.");
                  return;
                }
                BluetoothGattService service = connection.getService(SERVICE);
                if (code != BluetoothGatt.GATT_SUCCESS || service == null) {
                  finish(
                      "Unsupported camera: expected setup service was not found. No configuration"
                          + " sent.");
                  return;
                }
                write = service.getCharacteristic(WRITE);
                BluetoothGattCharacteristic notify = service.getCharacteristic(NOTIFY);
                if (write == null
                    || notify == null
                    || (write.getProperties() & BluetoothGattCharacteristic.PROPERTY_WRITE) == 0) {
                  finish("Unsupported camera setup characteristics. No configuration sent.");
                  return;
                }
                BluetoothGattDescriptor descriptor = notify.getDescriptor(CCCD);
                if (descriptor == null || !connection.setCharacteristicNotification(notify, true)) {
                  finish("Could not subscribe to camera replies. No configuration sent.");
                  return;
                }
                byte[] value =
                    (notify.getProperties() & BluetoothGattCharacteristic.PROPERTY_NOTIFY) != 0
                        ? BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE
                        : BluetoothGattDescriptor.ENABLE_INDICATION_VALUE;
                boolean started;
                if (Build.VERSION.SDK_INT >= 33)
                  started =
                      connection.writeDescriptor(descriptor, value) == BluetoothStatusCodes.SUCCESS;
                else {
                  descriptor.setValue(value);
                  started = connection.writeDescriptor(descriptor);
                }
                if (!started)
                  finish("Could not subscribe to camera replies. No configuration sent.");
              });
        }

        @Override
        public void onDescriptorWrite(
            BluetoothGatt connection, BluetoothGattDescriptor descriptor, int code) {
          handler.post(
              () -> {
                if (connection != gatt) return;
                if (code != BluetoothGatt.GATT_SUCCESS)
                  finish("Camera reply subscription failed. No configuration sent.");
                else sendChunk();
              });
        }

        @Override
        public void onCharacteristicWrite(
            BluetoothGatt connection, BluetoothGattCharacteristic characteristic, int code) {
          handler.post(
              () -> {
                if (connection != gatt) return;
                if (code != BluetoothGatt.GATT_SUCCESS) {
                  finish(
                      submitted
                          ? "Final write was interrupted. Settings may have been applied; verify"
                                + " the server before retrying."
                          : "Setup write failed. Verify network state before retrying.");
                  return;
                }
                if (cursor < packet.length)
                  handler.postDelayed(
                      () -> {
                        if (connection == gatt) sendChunk();
                      },
                      150);
                else {
                  password.setText("");
                  message(
                      "All settings written. Waiting briefly for the camera to switch networks…");
                  handler.postDelayed(
                      () -> {
                        if (connection == gatt)
                          finish(
                              "Settings written. Open the viewer to verify the camera joined your"
                                  + " Wi-Fi and server.");
                      },
                      5000);
                }
              });
        }
      };

  private void sendChunk() {
    if (Build.VERSION.SDK_INT >= 31
        && checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT)
            != PackageManager.PERMISSION_GRANTED) {
      finish("Bluetooth permission was revoked.");
      return;
    }
    byte[] chunk = Arrays.copyOfRange(packet, cursor, Math.min(cursor + 20, packet.length));
    boolean started;
    if (Build.VERSION.SDK_INT >= 33)
      started =
          gatt.writeCharacteristic(write, chunk, BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT)
              == BluetoothStatusCodes.SUCCESS;
    else {
      write.setWriteType(BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT);
      write.setValue(chunk);
      started = gatt.writeCharacteristic(write);
    }
    if (!started) {
      finish("Bluetooth rejected a setup write. Check the server before retrying.");
      return;
    }
    cursor += chunk.length;
    submitted = cursor == packet.length;
    message("Sending settings: " + cursor + " / " + packet.length + " bytes");
  }

  private void stopScan() {
    try {
      if (scanner != null) scanner.stopScan(scanCallback);
    } catch (SecurityException ignored) {
    }
  }

  private void closeConnection() {
    if (gatt != null) {
      BluetoothGatt old = gatt;
      gatt = null;
      try {
        old.disconnect();
      } catch (SecurityException ignored) {
      }
      try {
        old.close();
      } catch (SecurityException ignored) {
      }
    }
  }

  private void finish(String text) {
    busy = false;
    generation++;
    provision.setEnabled(true);
    scan.setEnabled(true);
    closeConnection();
    if (submitted) password.setText("");
    if (packet != null) Arrays.fill(packet, (byte) 0);
    packet = null;
    message(text);
  }

  @Override
  protected void onDestroy() {
    stopScan();
    handler.removeCallbacksAndMessages(null);
    closeConnection();
    if (packet != null) Arrays.fill(packet, (byte) 0);
    super.onDestroy();
  }
}
