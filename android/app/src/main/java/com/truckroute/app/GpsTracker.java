package com.truckroute.app;

import android.content.Context;
import android.location.Location;
import android.location.LocationListener;
import android.location.LocationManager;
import android.os.Bundle;
import android.os.Looper;

import org.json.JSONObject;

import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;

/** Single owner of location updates for the whole app.
 *
 *  v3.1 follow-quality fix: v3.0 requested GPS at 2000ms/5m and the WebView
 *  POLLED for it every 2000ms, so the puck could lag ~4s and jumped fix to
 *  fix. This tracker requests 1000ms/0m and PUSHES every fix to listeners
 *  (the bridge forwards them straight into the page). Both the activity's
 *  bridge and the foreground NavService start it, so fixes keep flowing
 *  with the screen off or the activity paused. */
public class GpsTracker {

    public interface FixListener {
        void onFix(Location loc);
    }

    private static volatile GpsTracker instance;

    private final LocationManager locationManager;
    private volatile Location lastLocation;
    private volatile boolean running = false;
    private final List<FixListener> listeners = new CopyOnWriteArrayList<>();

    private final LocationListener listener = new LocationListener() {
        @Override
        public void onLocationChanged(Location location) {
            if (location == null) return;
            // Keep the freshest fix; GPS wins ties over network.
            Location prev = lastLocation;
            if (prev != null && location.getTime() < prev.getTime()) return;
            lastLocation = location;
            for (FixListener l : listeners) {
                try { l.onFix(location); } catch (Exception ignored) {}
            }
        }

        @Override public void onStatusChanged(String p, int s, Bundle e) {}
        @Override public void onProviderEnabled(String p) {}
        @Override public void onProviderDisabled(String p) {}
    };

    private GpsTracker(Context ctx) {
        this.locationManager =
                (LocationManager) ctx.getSystemService(Context.LOCATION_SERVICE);
    }

    public static GpsTracker get(Context ctx) {
        GpsTracker local = instance;
        if (local == null) {
            synchronized (GpsTracker.class) {
                local = instance;
                if (local == null) {
                    local = new GpsTracker(ctx.getApplicationContext());
                    instance = local;
                }
            }
        }
        return local;
    }

    public synchronized void start() {
        if (running) return;
        Looper looper = Looper.getMainLooper();
        try {
            // High-accuracy nav-grade stream: ~1 fix/sec, no distance gate
            // (a distance gate is what made slow turns feel unresponsive).
            locationManager.requestLocationUpdates(
                    LocationManager.GPS_PROVIDER, 1000, 0, listener, looper);
            running = true;
        } catch (SecurityException | IllegalArgumentException ignored) {}
        try {
            locationManager.requestLocationUpdates(
                    LocationManager.NETWORK_PROVIDER, 3000, 10, listener, looper);
        } catch (SecurityException | IllegalArgumentException ignored) {}
    }

    public synchronized void stop() {
        try {
            locationManager.removeUpdates(listener);
        } catch (SecurityException ignored) {}
        running = false;
    }

    public boolean isRunning() {
        return running;
    }

    public void addListener(FixListener l) {
        if (!listeners.contains(l)) listeners.add(l);
    }

    public void removeListener(FixListener l) {
        listeners.remove(l);
    }

    /** Latest fix as JSON, or empty string when none yet. */
    public String lastLocationJson() {
        Location loc = lastLocation;
        if (loc == null) {
            try {
                loc = locationManager.getLastKnownLocation(LocationManager.GPS_PROVIDER);
                if (loc == null)
                    loc = locationManager.getLastKnownLocation(LocationManager.NETWORK_PROVIDER);
            } catch (SecurityException ignored) {}
        }
        if (loc == null) return "";
        return toJson(loc);
    }

    public static String toJson(Location loc) {
        try {
            JSONObject o = new JSONObject();
            o.put("lat", loc.getLatitude());
            o.put("lng", loc.getLongitude());
            o.put("accuracy", loc.hasAccuracy() ? loc.getAccuracy() : 0);
            o.put("speed", loc.hasSpeed() ? loc.getSpeed() : 0);
            o.put("bearing", loc.hasBearing() ? loc.getBearing() : 0);
            o.put("time", loc.getTime());
            return o.toString();
        } catch (Exception e) {
            return "";
        }
    }
}
