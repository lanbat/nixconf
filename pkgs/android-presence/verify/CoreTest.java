package com.lanbat.presence;

import java.util.HashMap;
import java.util.Map;

/** A dependency-free check of the pure logic, run with plain java. */
public final class CoreTest {
    private static int failures;

    private static void check(String name, boolean ok, String detail) {
        if (ok) {
            System.out.println("ok   " + name);
        } else {
            failures++;
            System.out.println("FAIL " + name + (detail == null ? "" : ": " + detail));
        }
    }

    private static void eq(String name, Object want, Object got) {
        check(name, want == null ? got == null : want.equals(got), "wanted " + want + ", got " + got);
    }

    private static byte[] hex(String s) {
        byte[] out = new byte[s.length() / 2];
        for (int i = 0; i < out.length; i++) {
            out[i] = (byte) Integer.parseInt(s.substring(i * 2, i * 2 + 2), 16);
        }
        return out;
    }

    public static void main(String[] args) {
        // The real beacon (people.momchilov.bleBeacon) plus major/minor/txpower.
        byte[] advert = hex("02155affd0d59828435cb1774221519517e0" + "0001" + "0002" + "c5");
        eq("parses the iBeacon UUID", "5affd0d5-9828-435c-b177-4221519517e0", BeaconParser.uuid(advert));

        eq("rejects a truncated payload", null, BeaconParser.uuid(hex("0215")));
        eq("rejects a non-iBeacon type", null, BeaconParser.uuid(hex("03155affd0d59828435cb1774221519517e0")));
        eq("rejects null", null, BeaconParser.uuid(null));

        eq("normalizes a dashed uuid", "5affd0d59828435cb1774221519517e0",
                BeaconParser.normalize("5AFFD0D5-9828-435C-B177-4221519517E0"));
        eq("normalizes an already normal uuid", "5affd0d59828435cb1774221519517e0",
                BeaconParser.normalize("5affd0d59828435cb1774221519517e0"));
        eq("normalizes null", null, BeaconParser.normalize(null));

        BeaconTracker t = new BeaconTracker(20_000);
        t.observe("a", -60, 0);
        t.observe("a", -64, 1_000);
        t.observe("a", -62, 2_000);
        eq("odd-count median", -62, t.report(2_000).get("a"));

        t.observe("a", -80, 3_000);
        eq("even-count median", -63, t.report(3_000).get("a")); // -80,-64,-62,-60 -> (-64 + -62) / 2

        eq("stale beacon is pruned", null, t.report(30_000).get("a"));

        BeaconTracker m = new BeaconTracker(20_000);
        m.observe("a", -55, 0);
        m.observe("b", -70, 0);
        Map<String, Integer> r = m.report(1_000);
        eq("two beacons both reported", 2, r.size());
        eq("first beacon's rssi", -55, r.get("a"));
        eq("second beacon's rssi", -70, r.get("b"));

        System.out.println(failures == 0 ? "ALL PASS" : failures + " FAILED");
        if (failures != 0) {
            System.exit(1);
        }
    }
}
