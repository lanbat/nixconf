package com.lanbat.presence;

import android.bluetooth.BluetoothAdapter;
import android.bluetooth.BluetoothManager;
import android.bluetooth.le.BluetoothLeScanner;
import android.bluetooth.le.ScanCallback;
import android.bluetooth.le.ScanRecord;
import android.bluetooth.le.ScanResult;
import android.bluetooth.le.ScanSettings;
import android.content.Context;
import android.util.Log;

/** Continuous BLE scan, feeding every phone beacon it sees into a BeaconTracker. */
public final class BleScanner {
    private static final String TAG = "lanbat-presence";

    private final Context context;
    private final BeaconTracker tracker;
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
        try {
            scanner.startScan(null, settings, callback);
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
                tracker.observe(BeaconParser.normalize(uuid), result.getRssi(),
                        System.currentTimeMillis());
            }
        }
    };
}
