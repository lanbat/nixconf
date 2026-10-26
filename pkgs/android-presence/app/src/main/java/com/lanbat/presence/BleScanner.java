package com.lanbat.presence;

import android.bluetooth.BluetoothAdapter;
import android.bluetooth.BluetoothManager;
import android.bluetooth.le.BluetoothLeScanner;
import android.bluetooth.le.ScanCallback;
import android.bluetooth.le.ScanFilter;
import android.bluetooth.le.ScanRecord;
import android.bluetooth.le.ScanResult;
import android.bluetooth.le.ScanSettings;
import android.content.Context;
import android.util.Log;

import java.util.Collections;
import java.util.HashSet;
import java.util.Set;

/** Continuous BLE scan, feeding every phone beacon it sees into a BeaconTracker. */
public final class BleScanner {
    private static final String TAG = "lanbat-presence";

    private final Context context;
    private final BeaconTracker tracker;
    /** UUIDs already logged, so a nearby phone does not flood logcat. */
    private final Set<String> announced = new HashSet<>();
    private BluetoothLeScanner scanner;
    private boolean started;

    public BleScanner(Context context, BeaconTracker tracker) {
        this.context = context;
        this.tracker = tracker;
    }

    /** False when Bluetooth is absent or off (the service then keeps retrying). */
    public boolean start() {
        if (started) {
            return true;
        }
        BluetoothManager manager =
                (BluetoothManager) context.getSystemService(Context.BLUETOOTH_SERVICE);
        BluetoothAdapter adapter = manager == null ? null : manager.getAdapter();
        if (adapter == null || !adapter.isEnabled()) {
            Log.w(TAG, "bluetooth unavailable or off");
            return false;
        }
        scanner = adapter.getBluetoothLeScanner();
        if (scanner == null) {
            Log.w(TAG, "no BLE scanner");
            return false;
        }
        ScanSettings settings = new ScanSettings.Builder()
                .setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY)
                .build();
        // Android refuses *unfiltered* scans while the screen is off (the box
        // is a TV, usually on standby). An iBeacon filter is enough to be
        // allowed, and it costs nothing: only Apple adverts matter here.
        ScanFilter beacons = new ScanFilter.Builder()
                .setManufacturerData(0x004C,
                        new byte[] { 0x02, 0x15 },
                        new byte[] { (byte) 0xFF, (byte) 0xFF })
                .build();
        try {
            scanner.startScan(Collections.singletonList(beacons), settings, callback);
        } catch (SecurityException err) {
            Log.w(TAG, "scan not permitted (grant BLUETOOTH_SCAN): " + err.getMessage());
            scanner = null;
            return false;
        }
        started = true;
        Log.i(TAG, "scanning");
        return true;
    }

    /** False until a scan is running; the service retries while it is false. */
    public boolean isStarted() {
        return started;
    }

    public void stop() {
        started = false;
        if (scanner != null) {
            try {
                scanner.stopScan(callback);
            } catch (SecurityException ignored) {
                // permission revoked mid-run; the scan is gone anyway
            }
            scanner = null;
        }
    }

    private final ScanCallback callback = new ScanCallback() {
        @Override public void onScanResult(int callbackType, ScanResult result) {
            ScanRecord record = result.getScanRecord();
            if (record == null) {
                return;
            }
            byte[] manufacturer = record.getManufacturerSpecificData(0x004C);
            String uuid = BeaconParser.uuid(manufacturer);
            if (uuid != null) {
                String key = BeaconParser.normalize(uuid);
                if (announced.add(key)) {
                    Log.i(TAG, "hearing " + key);
                }
                tracker.observe(key, result.getRssi(), System.currentTimeMillis());
            }
        }
    };
}
