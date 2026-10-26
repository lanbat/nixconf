package com.lanbat.presence;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNull;

import org.junit.Test;

/** The same checks as verify/CoreTest.java, for the Gradle unit-test task. */
public class BeaconTest {
    private static byte[] hex(String text) {
        byte[] out = new byte[text.length() / 2];
        for (int i = 0; i < out.length; i++) {
            out[i] = (byte) Integer.parseInt(text.substring(i * 2, i * 2 + 2), 16);
        }
        return out;
    }

    @Test public void parsesTheIBeaconUuid() {
        byte[] advert = hex("02155affd0d59828435cb1774221519517e0" + "0001" + "0002" + "c5");
        assertEquals("5affd0d5-9828-435c-b177-4221519517e0", BeaconParser.uuid(advert));
    }

    @Test public void rejectsThingsThatAreNotIBeacons() {
        assertNull(BeaconParser.uuid(null));
        assertNull(BeaconParser.uuid(hex("0215")));
        assertNull(BeaconParser.uuid(hex("03155affd0d59828435cb1774221519517e0")));
    }

    @Test public void normalizesUuidsForLookup() {
        assertEquals("5affd0d59828435cb1774221519517e0",
                BeaconParser.normalize("5AFFD0D5-9828-435C-B177-4221519517E0"));
        assertNull(BeaconParser.normalize(null));
    }

    @Test public void reportsTheMedianRssi() {
        BeaconTracker tracker = new BeaconTracker(20_000);
        tracker.observe("a", -60, 0);
        tracker.observe("a", -64, 1_000);
        tracker.observe("a", -62, 2_000);
        assertEquals(Integer.valueOf(-62), tracker.report(2_000).get("a"));

        tracker.observe("a", -80, 3_000);
        assertEquals(Integer.valueOf(-63), tracker.report(3_000).get("a"));
    }

    @Test public void forgetsBeaconsOutsideTheWindow() {
        BeaconTracker tracker = new BeaconTracker(20_000);
        tracker.observe("a", -60, 0);
        assertNull(tracker.report(30_000).get("a"));
    }
}
