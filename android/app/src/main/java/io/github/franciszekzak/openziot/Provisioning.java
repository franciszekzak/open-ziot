package io.github.franciszekzak.openziot;

import java.io.ByteArrayOutputStream;
import java.nio.charset.StandardCharsets;

/** Independently implemented stock X5 BLE setup packet; no vendor binaries. */
public final class Provisioning {
  private Provisioning() {}

  private static void shortBE(ByteArrayOutputStream out, int value) {
    out.write((value >>> 8) & 255);
    out.write(value & 255);
  }

  private static void field(ByteArrayOutputStream out, String value, int maximum, String label) {
    byte[] bytes = value.getBytes(StandardCharsets.UTF_8);
    if (bytes.length == 0 || bytes.length > maximum)
      throw new IllegalArgumentException(label + " must be 1–" + maximum + " UTF-8 bytes");
    out.write(bytes.length);
    out.write(bytes, 0, bytes.length);
  }

  public static byte[] packet(
      String ssid,
      String password,
      String host,
      int port,
      int offsetMinutes,
      long registrationCode,
      int sequence) {
    if (!host.matches("[A-Za-z0-9.-]+") || host.length() > 100)
      throw new IllegalArgumentException(
          "Use a hostname or IPv4 address, without https:// or a path");
    if (port < 1 || port > 65535) throw new IllegalArgumentException("Server port must be 1–65535");
    if (offsetMinutes < -720 || offsetMinutes > 840)
      throw new IllegalArgumentException("UTC offset must be -720–840 minutes");
    ByteArrayOutputStream body = new ByteArrayOutputStream();
    shortBE(body, 1);
    field(body, ssid, 32, "Wi-Fi name");
    field(body, password, 64, "Wi-Fi password");
    field(body, host + ":" + port, 128, "Server endpoint");
    shortBE(body, offsetMinutes);
    for (int shift = 24; shift >= 0; shift -= 8)
      body.write((int) (registrationCode >>> shift) & 255);
    byte[] bytes = body.toByteArray();
    ByteArrayOutputStream out = new ByteArrayOutputStream();
    out.write(0x86);
    out.write(0x86);
    shortBE(out, bytes.length + 5);
    shortBE(out, sequence);
    shortBE(out, bytes.length);
    out.write(bytes, 0, bytes.length);
    int xor = 0;
    for (byte value : out.toByteArray()) xor ^= value & 255;
    out.write(xor);
    out.write(13);
    return out.toByteArray();
  }
}
