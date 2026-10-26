package com.lanbat.presence;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.os.Build;
import android.util.Log;

/**
 * Receives the provisioner's configuration and (re)starts the service:
 *
 * <pre>
 * adb shell am broadcast -a com.lanbat.presence.CONFIG \
 *   --es room "Living Room" \
 *   --es router_url "ws://192.168.1.10:8770/v1/body" \
 *   --es beacons '{"5affd0d5-9828-435c-b177-4221519517e0":"momchilov"}'
 * </pre>
 *
 * <p>The map is {@code uuid -> person key}, as the provisioner sends it. The
 * service is then started from adb too, since a background broadcast cannot
 * always start a foreground service.
 */
public final class ConfigReceiver extends BroadcastReceiver {
    public static final String ACTION = "com.lanbat.presence.CONFIG";

    @Override public void onReceive(Context context, Intent intent) {
        Config config = Config.fromBroadcast(
                intent.getStringExtra("room"),
                intent.getStringExtra("router_url"),
                intent.getStringExtra("beacons"),
                intent.getIntExtra("report_seconds", 2),
                intent.getIntExtra("window_seconds", 20));
        if (config == null) {
            Log.w(PresenceService.TAG,
                    "CONFIG ignored: room, router_url and a valid beacons JSON are required");
            return;
        }
        config.save(context);
        Log.i(PresenceService.TAG, "configured for " + config.room);
        startService(context);
    }

    static void startService(Context context) {
        Intent service = new Intent(context, PresenceService.class);
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(service);
            } else {
                context.startService(service);
            }
        } catch (RuntimeException err) {
            // Android 12+ refuses a foreground service started from the
            // background. The config is saved either way, and the provisioner
            // starts the service from adb, which is allowed.
            Log.w(PresenceService.TAG, "cannot start the service from here: " + err.getMessage());
        }
    }
}
