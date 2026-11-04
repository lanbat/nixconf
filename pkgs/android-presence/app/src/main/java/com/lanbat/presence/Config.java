package com.lanbat.presence;

import android.content.Context;
import android.content.SharedPreferences;

import org.json.JSONException;
import org.json.JSONObject;

import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.Map;

/** What the provisioner tells the app: the room, where the router is, and which
 *  beacon UUID belongs to which person. */
public final class Config {
    private static final String PREFS = "presence";
    private static final String KEY = "config";

    public final String room;
    public final String routerUrl;
    /** uuid (normalized: lower case, no dashes) -> person key. */
    public final Map<String, String> beacons;
    public final int reportSeconds;
    public final int windowSeconds;

    public Config(String room, String routerUrl, Map<String, String> beacons,
                  int reportSeconds, int windowSeconds) {
        this.room = room;
        this.routerUrl = routerUrl;
        this.beacons = beacons;
        this.reportSeconds = reportSeconds;
        this.windowSeconds = windowSeconds;
    }

    /** Build from a CONFIG broadcast. Null when the essentials are missing. */
    public static Config fromBroadcast(String room, String routerUrl, String beaconsJson,
                                       int reportSeconds, int windowSeconds) {
        if (room == null || room.trim().isEmpty()
                || routerUrl == null || routerUrl.trim().isEmpty()) {
            return null;
        }
        Map<String, String> beacons = new LinkedHashMap<>();
        try {
            JSONObject obj = beaconsJson == null || beaconsJson.trim().isEmpty()
                    ? new JSONObject() : new JSONObject(beaconsJson);
            Iterator<String> keys = obj.keys();
            while (keys.hasNext()) {
                String uuid = keys.next();
                beacons.put(BeaconParser.normalize(uuid), obj.getString(uuid));
            }
        } catch (JSONException err) {
            return null;
        }
        return new Config(room.trim(), routerUrl.trim(), beacons,
                reportSeconds > 0 ? reportSeconds : 2,
                windowSeconds > 0 ? windowSeconds : 20);
    }

    public static Config load(Context context) {
        String raw = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
                .getString(KEY, null);
        if (raw == null) {
            return null;
        }
        try {
            JSONObject obj = new JSONObject(raw);
            Map<String, String> beacons = new LinkedHashMap<>();
            JSONObject stored = obj.optJSONObject("beacons");
            if (stored != null) {
                Iterator<String> keys = stored.keys();
                while (keys.hasNext()) {
                    String uuid = keys.next();
                    beacons.put(BeaconParser.normalize(uuid), stored.getString(uuid));
                }
            }
            return new Config(obj.getString("room"), obj.getString("routerUrl"), beacons,
                    obj.optInt("reportSeconds", 2), obj.optInt("windowSeconds", 20));
        } catch (JSONException err) {
            return null;
        }
    }

    public void save(Context context) {
        try {
            JSONObject obj = new JSONObject();
            obj.put("room", room);
            obj.put("routerUrl", routerUrl);
            JSONObject stored = new JSONObject();
            for (Map.Entry<String, String> entry : beacons.entrySet()) {
                stored.put(entry.getKey(), entry.getValue());
            }
            obj.put("beacons", stored);
            obj.put("reportSeconds", reportSeconds);
            obj.put("windowSeconds", windowSeconds);
            SharedPreferences.Editor editor = context
                    .getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit();
            editor.putString(KEY, obj.toString()).apply();
        } catch (JSONException ignored) {
            // every value is a string or an int; this cannot fail
        }
    }
}
