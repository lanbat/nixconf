package com.lanbat.presence;

import android.util.Log;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.util.Map;
import java.util.concurrent.TimeUnit;

import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;
import okhttp3.WebSocket;
import okhttp3.WebSocketListener;

/**
 * The assistant-router's room-sensor socket, the same protocol
 * {@code pkgs/room-presence/room_presence.py} speaks: a hello, then
 * {"seen": [{"person": …, "rssi": …}]} every report.
 */
public final class RouterClient {
    private static final String TAG = "lanbat-presence";
    private static final int PROTO = 1;

    private final String url;
    private final String room;
    private final OkHttpClient client = new OkHttpClient.Builder()
            .pingInterval(20, TimeUnit.SECONDS)
            .build();

    private WebSocket socket;
    private boolean connecting;
    private boolean closed;

    public RouterClient(String url, String room) {
        this.url = url;
        this.room = room;
    }

    /** Send the medians, keyed by UUID; connects first if needed. A failed or
     *  closed socket is retried on the next call, so no timers live here. */
    public void send(Map<String, Integer> rssiByUuid, Map<String, String> uuidToPerson) {
        if (closed) {
            return;
        }
        WebSocket ws = socket;
        if (ws == null) {
            connect();
            return;
        }
        try {
            JSONArray seen = new JSONArray();
            for (Map.Entry<String, Integer> entry : rssiByUuid.entrySet()) {
                String person = uuidToPerson.get(entry.getKey());
                if (person == null) {
                    continue;
                }
                JSONObject sighting = new JSONObject();
                sighting.put("person", person);
                sighting.put("rssi", entry.getValue().intValue());
                seen.put(sighting);
            }
            if (seen.length() == 0) {
                return;
            }
            JSONObject message = new JSONObject();
            message.put("seen", seen);
            ws.send(message.toString());
        } catch (JSONException ignored) {
            // strings and ints only; cannot fail
        }
    }

    /** Open the socket now, so the hello goes out before any beacon is heard. */
    public void connect() {
        if (closed || connecting) {
            return;
        }
        connecting = true;
        Request request = new Request.Builder().url(url).build();
        socket = client.newWebSocket(request, new WebSocketListener() {
            @Override public void onOpen(WebSocket ws, Response response) {
                connecting = false;
                Log.i(TAG, "room sensor of " + room + " at " + url);
                try {
                    JSONObject hello = new JSONObject();
                    JSONObject body = new JSONObject();
                    body.put("room", room);
                    body.put("kind", "room-sensor");
                    body.put("proto", PROTO);
                    hello.put("hello", body);
                    ws.send(hello.toString());
                } catch (JSONException ignored) {
                    // strings and ints only
                }
            }

            @Override public void onFailure(WebSocket ws, Throwable t, Response response) {
                connecting = false;
                socket = null;
                Log.w(TAG, "router unreachable (" + t.getMessage() + ")");
            }

            @Override public void onClosed(WebSocket ws, int code, String reason) {
                connecting = false;
                socket = null;
            }
        });
    }

    public void close() {
        closed = true;
        WebSocket ws = socket;
        socket = null;
        if (ws != null) {
            ws.close(1000, "stop");
        }
        client.dispatcher().executorService().shutdown();
    }
}
