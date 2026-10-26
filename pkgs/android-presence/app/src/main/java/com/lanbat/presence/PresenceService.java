package com.lanbat.presence;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.os.Build;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.os.PowerManager;
import android.util.Log;

import java.util.Map;

/**
 * Foreground service: scans BLE continuously and reports the median RSSI of each
 * configured phone beacon to the assistant-router, the way a room-presence
 * scanner does.
 */
public final class PresenceService extends Service {
    static final String TAG = "lanbat-presence";
    private static final String CHANNEL = "presence";
    private static final int NOTIFICATION_ID = 1;

    private final Handler handler = new Handler(Looper.getMainLooper());
    private final Runnable tick = new Runnable() {
        @Override public void run() {
            if (config == null) {
                stopSelf();
                return;
            }
            if (scanner != null && !scanner.isStarted()) {
                scanner.start(); // Bluetooth may have been off at startup
            }
            Map<String, Integer> report = tracker.report(System.currentTimeMillis());
            if (router != null) {
                router.send(report, config.beacons);
            }
            updateNotification(report.size());
            handler.postDelayed(this, config.reportSeconds * 1000L);
        }
    };

    private Config config;
    private BeaconTracker tracker;
    private BleScanner scanner;
    private RouterClient router;
    private PowerManager.WakeLock wakeLock;

    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        Config cfg = Config.load(this);
        if (cfg == null) {
            Log.w(TAG, "not configured; waiting for a CONFIG broadcast");
            stopSelf();
            return START_NOT_STICKY;
        }
        if (config != null) {
            teardown();
        }
        config = cfg;
        tracker = new BeaconTracker(cfg.windowSeconds * 1000L);
        scanner = new BleScanner(this, tracker);
        router = new RouterClient(cfg.routerUrl, cfg.room);

        createChannel();
        startInForeground();
        acquireWakeLock();
        scanner.start();
        router.connect(); // hello now; sightings follow once beacons are heard
        handler.post(tick);
        return START_STICKY;
    }

    @Override public void onDestroy() {
        handler.removeCallbacks(tick);
        teardown();
        super.onDestroy();
    }

    private void teardown() {
        handler.removeCallbacks(tick);
        if (scanner != null) {
            scanner.stop();
            scanner = null;
        }
        if (router != null) {
            router.close();
            router = null;
        }
        if (wakeLock != null && wakeLock.isHeld()) {
            wakeLock.release();
        }
        wakeLock = null;
        stopForeground(STOP_FOREGROUND_REMOVE);
    }

    private void createChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationManager manager = getSystemService(NotificationManager.class);
            if (manager != null) {
                manager.createNotificationChannel(new NotificationChannel(
                        CHANNEL, "Presence", NotificationManager.IMPORTANCE_LOW));
            }
        }
    }

    private Notification notification(String text) {
        return new Notification.Builder(this, CHANNEL)
                .setContentTitle("Room presence")
                .setContentText(text)
                .setSmallIcon(android.R.drawable.stat_sys_data_bluetooth)
                .setOngoing(true)
                .build();
    }

    private void startInForeground() {
        Notification notification = notification("Scanning for phones in " + config.room);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(NOTIFICATION_ID, notification,
                    ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE);
        } else {
            startForeground(NOTIFICATION_ID, notification);
        }
    }

    private void updateNotification(int beacons) {
        NotificationManager manager = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        if (manager != null) {
            manager.notify(NOTIFICATION_ID, notification(config.room + ": " + beacons + " beacon(s)"));
        }
    }

    private void acquireWakeLock() {
        PowerManager manager = (PowerManager) getSystemService(POWER_SERVICE);
        if (manager != null) {
            wakeLock = manager.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "lanbat:presence");
            wakeLock.acquire();
        }
    }

    @Override public IBinder onBind(Intent intent) {
        return null;
    }
}
