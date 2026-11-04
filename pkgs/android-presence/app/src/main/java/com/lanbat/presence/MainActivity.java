package com.lanbat.presence;

import android.app.Activity;
import android.os.Bundle;
import android.widget.TextView;

/** A status screen for debugging on the box: what was configured and whether it
 *  is worth looking at the notification. */
public final class MainActivity extends Activity {
    @Override protected void onCreate(Bundle state) {
        super.onCreate(state);
        TextView view = new TextView(this);
        view.setPadding(64, 64, 64, 64);
        view.setTextSize(18);
        Config config = Config.load(this);
        if (config == null) {
            view.setText("Room presence\n\nNot configured yet.\n"
                    + "The provisioner sends the room, the router URL and the beacon UUIDs.");
        } else {
            view.setText("Room presence\n\n"
                    + "Room: " + config.room + "\n"
                    + "Router: " + config.routerUrl + "\n"
                    + "Beacons: " + config.beacons.size() + "\n"
                    + "Reports every " + config.reportSeconds + " s, "
                    + "median over " + config.windowSeconds + " s");
        }
        setContentView(view);
    }
}
