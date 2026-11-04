package com.lanbat.presence;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/** Starts the service after a reboot or an app update, once configured. */
public final class BootReceiver extends BroadcastReceiver {
    @Override public void onReceive(Context context, Intent intent) {
        if (Config.load(context) != null) {
            ConfigReceiver.startService(context);
        }
    }
}
