package com.truckroute.app;

import android.content.Context;
import android.content.Intent;
import android.location.Location;
import android.location.LocationListener;
import android.location.LocationManager;
import android.os.Build;
import android.os.Bundle;
import android.os.Looper;
import android.speech.tts.TextToSpeech;
import android.webkit.JavascriptInterface;

import org.json.JSONObject;

import java.util.Locale;

/** JS bridge: native GPS + TTS + foreground nav service for the WebView app. */
public class TruckRouteBridge {

    private final Context ctx;
    private final LocationManager locationManager;
    private volatile Location lastLocation;
    private TextToSpeech tts;
    private volatile boolean ttsReady = false;

    private final LocationListener listener = new LocationListener() {
        @Override
        public void onLocationChanged(Location location) {
            lastLocation = location;
        }

        @Override public void onStatusChanged(String p, int s, Bundle e) {}
        @Override public void onProviderEnabled(String p) {}
        @Override public void onProviderDisabled(String p) {}
    };

    public TruckRouteBridge(Context ctx) {
        this.ctx = ctx.getApplicationContext();
        this.locationManager =
                (LocationManager) this.ctx.getSystemService(Context.LOCATION_SERVICE);
        tts = new TextToSpeech(this.ctx, status -> {
            if (status == TextToSpeech.SUCCESS) {
                tts.setLanguage(Locale.US);
                ttsReady = true;
            }
        });
    }

    // ---- GPS ----
    @JavascriptInterface
    public void startGps() {
        try {
            Looper looper = Looper.getMainLooper();
            locationManager.requestLocationUpdates(
                    LocationManager.GPS_PROVIDER, 2000, 5, listener, looper);
        } catch (SecurityException ignored) {}
        try {
            Looper looper = Looper.getMainLooper();
            locationManager.requestLocationUpdates(
                    LocationManager.NETWORK_PROVIDER, 5000, 25, listener, looper);
        } catch (SecurityException ignored) {}
    }

    @JavascriptInterface
    public void stopGps() {
        try {
            locationManager.removeUpdates(listener);
        } catch (SecurityException ignored) {}
    }

    /** Latest fix as JSON, or empty string when none yet. */
    @JavascriptInterface
    public String getLastLocation() {
        Location loc = lastLocation;
        if (loc == null) {
            // fall back to last-known so the app works immediately-ish
            try {
                loc = locationManager.getLastKnownLocation(LocationManager.GPS_PROVIDER);
                if (loc == null)
                    loc = locationManager.getLastKnownLocation(LocationManager.NETWORK_PROVIDER);
            } catch (SecurityException ignored) {}
        }
        if (loc == null) return "";
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

    // ---- TTS ----
    @JavascriptInterface
    public void speak(String text) {
        if (text == null || text.isEmpty()) return;
        if (!ttsReady || tts == null) return;
        if (Build.VERSION.SDK_INT >= 21) {
            tts.speak(text, TextToSpeech.QUEUE_FLUSH, null, "tr-nav");
        } else {
            tts.speak(text, TextToSpeech.QUEUE_FLUSH, null);
        }
    }

    @JavascriptInterface
    public void stopSpeaking() {
        if (tts != null) tts.stop();
    }

    // ---- foreground nav service (keeps voice guidance alive with screen off) ----
    @JavascriptInterface
    public void startNavService() {
        Intent i = new Intent(ctx, NavService.class);
        if (Build.VERSION.SDK_INT >= 26) ctx.startForegroundService(i);
        else ctx.startService(i);
    }

    @JavascriptInterface
    public void stopNavService() {
        ctx.stopService(new Intent(ctx, NavService.class));
    }

    public void shutdown() {
        stopGps();
        if (tts != null) {
            tts.stop();
            tts.shutdown();
            tts = null;
        }
    }
}
