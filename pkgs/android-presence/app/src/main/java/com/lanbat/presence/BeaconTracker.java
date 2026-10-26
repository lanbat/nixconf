package com.lanbat.presence;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Signal strengths per beacon over a window, reported as a median.
 *
 * <p>This mirrors {@code pkgs/room-presence/room_presence.py}'s {@code Tracker},
 * so the assistant-router sees the same kind of report from the TV box as from
 * the Pi scanners: a median over the recent adverts, not a single noisy one.
 */
public final class BeaconTracker {
    private final long windowMs;
    /** uuid -> [(whenMs, rssi)]. */
    private final Map<String, List<long[]>> seen = new HashMap<>();

    public BeaconTracker(long windowMs) {
        this.windowMs = windowMs;
    }

    public void observe(String uuid, int rssi, long nowMs) {
        if (uuid == null) {
            return;
        }
        List<long[]> samples = seen.get(uuid);
        if (samples == null) {
            samples = new ArrayList<>();
            seen.put(uuid, samples);
        }
        samples.add(new long[] { nowMs, rssi });
    }

    /** uuid -> median RSSI for every beacon heard within the window; stale
     *  samples and empty beacons are pruned as a side effect. */
    public Map<String, Integer> report(long nowMs) {
        Map<String, Integer> out = new LinkedHashMap<>();
        Iterator<Map.Entry<String, List<long[]>>> it = seen.entrySet().iterator();
        while (it.hasNext()) {
            Map.Entry<String, List<long[]>> entry = it.next();
            List<long[]> recent = new ArrayList<>();
            for (long[] sample : entry.getValue()) {
                if (nowMs - sample[0] <= windowMs) {
                    recent.add(sample);
                }
            }
            if (recent.isEmpty()) {
                it.remove();
                continue;
            }
            long[] rssis = new long[recent.size()];
            for (int i = 0; i < rssis.length; i++) {
                rssis[i] = recent.get(i)[1];
            }
            Arrays.sort(rssis);
            int mid = rssis.length / 2;
            long median = rssis.length % 2 == 1
                    ? rssis[mid]
                    : Math.round((rssis[mid - 1] + rssis[mid]) / 2.0);
            out.put(entry.getKey(), (int) median);
            entry.setValue(recent);
        }
        return out;
    }
}
