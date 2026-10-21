# Wi-Fi: extending the home network

Where the router's Wi-Fi is weak (upstairs, at the far end of the house), a wired host
can serve the home network itself from a USB Wi-Fi dongle: the same network name and
password, so phones, laptops and the Stack-chans roam onto it, on a channel the router
doesn't use. It is an access point, not a repeater: its link back is the cable, so it
doesn't halve the speed the way a repeater does. Its clients get their addresses from
the router's DHCP, as before; nothing is routed on the host.

## What it needs

- **A wired host with power to spare.** The server is one, and a Pi 5 is another (USB 3,
  gigabit Ethernet; see the power note below). A Pi 3 is too weak for it (USB 2.0, 100 Mbit
  Ethernet, little power); a Pi 3 with a dongle listens instead (room presence).
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

- **Repeater or access point.** This is an access point on a cable, which is what a wired
  host should be: a repeater joins the router over the air and halves the speed. The
  chip can do both at once (`iw phy` lists `#{ managed } <= 2, #{ AP } <= 1` on one
  channel), so a wireless repeater on a host with no cable is possible, but nothing here
  sets one up.
- **More than one network (SSID) per dongle: no.** The MT7921AU allows one access-point
  interface (`#{ AP } <= 1`), and a second SSID on one radio needs a second. Another network
  means another dongle, with its own interface and channel.
- **VLANs.** The driver lists `AP/VLAN`, so hostapd's per-client VLANs should work with
  one SSID: a passphrase file (`wpa_psk_file`) gives each passphrase its own `vlanid`, and
  clients with different passphrases land on different VLANs of the bridge. Nothing here
  does it yet, and it has not been tried on this chip. Two things are needed before it
  is worth it: the bridge's uplink has to carry the tagged VLANs to somewhere that
  understands them (a VLAN-aware switch or router; most home routers do not), and
  the VLANs need their own addressing and firewall rules there. A single SSID that
  puts different people on different networks is what this gives; different SSIDs for
  different networks needs the second dongle above.
- **On a Pi 5 the dongle needs a powered hub.** A Pi 5 on a 3 A supply shares 600 mA
  among its USB ports, and the dongle shares them with a satellite's microphone and a USB
  speaker. A self-powered hub reports `bMaxPower` 0 mA in `/sys/bus/usb/devices/*/`.
- **Roaming** is the phone's choice. Phones and laptops move to the stronger access point
  when the signal drops; the Stack-chans pick the strongest when they connect and reconnect (firmware 1.10.1:
  a scan of every channel, sorted by signal); they don't roam while connected.
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
