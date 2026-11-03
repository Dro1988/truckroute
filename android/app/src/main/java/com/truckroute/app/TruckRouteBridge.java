package com.truckroute.app;

import android.content.Context;
import android.content.Intent;
import android.location.Location;
import android.os.Build;
import android.speech.tts.TextToSpeech;
import android.webkit.JavascriptInterface;
import android.webkit.WebView;

import java.util.Locale;

/** JS bridge: native GPS + TTS + foreground nav service for the WebView app.
 *
 *  GPS (v3.1): fixes are PUSHED into the page the moment they arrive
 *  (window.TruckRouteNative.onLocation) instead of the page polling every
 *  2s — polling + a 2s/5m native stream was the v3.0 follow lag. Polling
 *  via getLastLocation() remains as a fallback. */
public class TruckRouteBridge {

    private final Context ctx;
    private final GpsTracker tracker;
    private volatile WebView webView;
    private TextToSpeech tts;
    private volatile boolean ttsReady = false;

    private final GpsTracker.FixListener pushListener = new GpsTracker.FixListener() {
        @Override
        public void onFix(Location loc) {
            WebView wv = webView;
            if (wv == null) return;
            final String json = GpsTracker.toJson(loc);
            if (json.isEmpty()) return;
            wv.post(() -> {
                try {
                    wv.evaluateJavascript(
                            "window.TruckRouteNative && window.TruckRouteNative.onLocation("
                                    + json + ")", null);
                } catch (Exception ignored) {}
            });
        }
    };

    public TruckRouteBridge(Context ctx) {
        this.ctx = ctx.getApplicationContext();
        this.tracker = GpsTracker.get(this.ctx);
        this.tracker.addListener(pushListener);
        tts = new TextToSpeech(this.ctx, status -> {
            if (status == TextToSpeech.SUCCESS) {
                tts.setLanguage(Locale.US);
                ttsReady = true;
            }
        });
    }

    public void attachWebView(WebView wv) {
        this.webView = wv;
    }

    // ---- GPS ----
    @JavascriptInterface
    public void startGps() {
        tracker.start();
    }

    @JavascriptInterface
    public void stopGps() {
        // Only stop the stream when navigation isn't relying on the
        // foreground service; the service manages its own lifecycle.
        if (!NavService.isRunning()) tracker.stop();
    }

    /** Latest fix as JSON, or empty string when none yet. */
    @JavascriptInterface
    public String getLastLocation() {
        return tracker.lastLocationJson();
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

    // ---- foreground nav service (keeps GPS + voice alive with screen off) ----
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
        tracker.removeListener(pushListener);
        if (!NavService.isRunning()) tracker.stop();
        if (tts != null) {
            tts.stop();
            tts.shutdown();
            tts = null;
        }
    }
}
