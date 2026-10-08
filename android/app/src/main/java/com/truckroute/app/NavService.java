package com.truckroute.app;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Intent;
import android.os.Build;
import android.os.IBinder;

/** Foreground service: keeps the process (and voice guidance) alive during
 *  navigation, including with the screen off. Location type service. */
public class NavService extends Service {

    private static final String CHANNEL_ID = "truckroute-nav";
    private static final int NOTIF_ID = 42;

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
        return START_STICKY;
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    @Override
    public void onDestroy() {
        stopForeground(true);
        super.onDestroy();
    }
}
