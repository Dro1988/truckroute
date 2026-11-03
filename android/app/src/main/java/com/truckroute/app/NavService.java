package com.truckroute.app;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Intent;
import android.os.Build;
import android.os.IBinder;

/** Foreground service: keeps navigation alive during a drive, including
 *  with the screen off. Location-type service.
 *
 *  v3.1: the service itself starts the shared GpsTracker stream, so GPS
 *  fixes keep flowing at nav rate even when the activity is paused or the
 *  WebView is throttled — in v3.0 the service only held a notification
 *  while location updates lived and died with the activity. */
public class NavService extends Service {

    private static final String CHANNEL_ID = "truckroute-nav";
    private static final int NOTIF_ID = 42;
    private static volatile boolean running = false;

    public static boolean isRunning() {
        return running;
    }

    @Override
    public void onCreate() {
        super.onCreate();
        if (Build.VERSION.SDK_INT >= 26) {
            NotificationChannel ch = new NotificationChannel(
                    CHANNEL_ID, "Navigation", NotificationManager.IMPORTANCE_LOW);
            ch.setDescription("Active TruckRoute navigation");
            NotificationManager nm = getSystemService(NotificationManager.class);
            if (nm != null) nm.createNotificationChannel(ch);
        }
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        Notification.Builder b = Build.VERSION.SDK_INT >= 26
                ? new Notification.Builder(this, CHANNEL_ID)
                : new Notification.Builder(this);
        b.setContentTitle("TruckRoute navigation active")
         .setContentText("Voice guidance is on. Drive safe.")
         .setSmallIcon(android.R.drawable.ic_dialog_map)
         .setOngoing(true);
        startForeground(NOTIF_ID, b.build());
        running = true;
        // Own the GPS stream while navigating so background/screen-off
        // doesn't degrade tracking.
        GpsTracker.get(getApplicationContext()).start();
        return START_STICKY;
    }

    @Override
    public void onDestroy() {
        running = false;
        stopForeground(true);
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}
