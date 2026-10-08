package io.github.franciszekzak.openziot;

import static org.junit.Assert.*;

import org.junit.Test;

public class ProvisioningTest {
  @Test
  public void matchesIndependentPythonVector() {
    byte[] actual =
        Provisioning.packet(
            "TestWiFi", "example-password", "camera.example.org", 18882, 120, 0x12345678L, 1);
    StringBuilder hex = new StringBuilder();
    for (byte value : actual) hex.append(String.format("%02x", value & 255));
    assertEquals(
        "868600400001003b0001085465737457694669106578616d706c652d70617373776f72641863616d6572612e6578616d706c652e6f72673a31383838320078123456787c0d",
        hex.toString());
  }

  @Test(expected = IllegalArgumentException.class)
  public void rejectsOverlongUtf8Ssid() {
    Provisioning.packet("é".repeat(17), "password", "camera.example.org", 18882, 0, 1, 1);
  }

  @Test(expected = IllegalArgumentException.class)
  public void rejectsUrlInsteadOfHost() {
    Provisioning.packet("wifi", "password", "https://camera.example.org", 18882, 0, 1, 1);
  }

  @Test(expected = IllegalArgumentException.class)
  public void rejectsInvalidPort() {
    Provisioning.packet("wifi", "password", "camera.example.org", 65536, 0, 1, 1);
  }
}
