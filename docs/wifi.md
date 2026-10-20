# Wi-Fi: extending the home network

Where the router's Wi-Fi is weak (upstairs, at the far end of the house), a wired host
can serve the home network itself from a USB Wi-Fi dongle: the same network name and
password, so phones, laptops and the Stack-chans roam onto it, on a channel the router
doesn't use. It is an access point, not a repeater: its link back is the cable, so it
doesn't halve the speed the way a repeater does. Its clients get their addresses from
the router's DHCP, as before; nothing is routed on the host.

## What it needs

- **A wired host with power to spare.** The server is the one. A Pi 3 is too weak for
  it (USB 2.0, 100 Mbit Ethernet, little power); a Pi with a dongle listens instead
  (room presence).
- **A dongle with an in-kernel driver that does access-point mode.** MediaTek MT7921AU
  (`mt7921u`, sold as "AX1800" Wi-Fi 6 adapters) works: on the server it served a test
  network at 130 Mbit/s and reported each client's signal. On a Raspberry Pi it needs
  a powered USB hub: a Pi 5 shares 600 mA among its USB ports.
- **The home network's password** in a secret, `wifi-access-point`, one line
  ([secrets/README.md](../secrets/README.md)).

## Turning it on

In the host's deploy entry (`modules`):

```nix
{
  # The host's address moves from its port onto a bridge, which the access point's
  # clients join; extraPorts adds other wired ports (a host plugged in behind this one).
  lanbat.network.bridge = {
    enable = true;
    macAddress = "6c:2b:59:00:00:01"; # the port's (`ip link`), so the LAN keeps knowing the host
    extraPorts = [ "enp2s0" ];
  };
  lanbat.wifi.accessPoint = {
    enable = true;
    interface = "wlp0s20f0u1"; # the dongle, as `ip link` names it (it follows the USB port)
    ssid = "<the router's network name>";
    countryCode = "GB";
    channel = 6;               # one the router doesn't use: 1, 6 or 11 on 2.4 GHz
  };
}
```

Deploy with deploy-rs as usual. Moving the address onto the bridge is a network change:
if the host doesn't answer afterwards, deploy-rs rolls it back by itself.

Modules that listen on the LAN (Wi-Fi presence, LAN DNS, the voice satellite) follow the
address onto the bridge: they read `lanbat.network.lanInterface`.

With the room-presence plugin on the host, its room sensor also reads the access point's
clients (`iw station dump`) and reports the household's phones among them, with their
signal: Wi-Fi presence for that room ([people.md](people.md#phones-at-home)).

## Things to know

- **A host behind an extra port** is on the LAN as if through a switch, keeping its
  address; it is offline while this host restarts.
- **Roaming** is the phone's choice. Phones and laptops move to the stronger access point
  when the signal drops; the Stack-chans pick the strongest when they connect.
- **Dozing clients on an MT7921 access point.** The MT7921 never flags, in its beacons,
  the frames it holds for a client in power saving (a capture showed none of ~800
  beacons set the client's bit; `mt76_set_tim()` is empty and the chip's firmware
  doesn't update it). A dozing client gets its frames only when it next transmits.
  Phones transmit often enough not to notice. The Stack-chans send a keep-alive every
  200 ms for this (firmware 1.9.9). Other low-power devices on it (ESPHome and the like)
  may lag: turn their Wi-Fi power saving off, or keep them on the router.
- **Security** is the router's: WPA2-Personal with the same password (`security`,
  `wpa2-sha1` by default, as most routers serve it). The password stays in its secret,
  never in the Nix store.
