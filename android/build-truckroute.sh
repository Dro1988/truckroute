#!/bin/bash
# TruckRoute APK build — manual pipeline (no Gradle: Java TCP is blocked here).
# Toolchain: aapt2 (native) + javac/d8/apksigner (local JVM, no network).
# Framework-only app: zero external dependencies.
set -e
export JAVA_HOME=/home/hatch/workspace/.tools/jdk/jdk-21.0.12.1+1
export PATH=$JAVA_HOME/bin:$PATH
SDK=/home/hatch/workspace/.tools/android-sdk
BT=$SDK/build-tools/35.0.0
AAPT2=$BT/aapt2
ANDROID_JAR=$SDK/platforms/android-36/android.jar
REPO=/home/hatch/workspace/goals/truckroute-truck-gps-app-mvp/repo
PROJ=$REPO/android
BUILD=/home/hatch/workspace/.tools/truckroute-build
APPID=com.truckroute.app
VERCODE=${VERCODE:-1}
VERNAME=${VERNAME:-1.0}
API_BASE=${API_BASE:-https://truckroute-api.onrender.com}

rm -rf "$BUILD"
mkdir -p "$BUILD/compiled" "$BUILD/gen" "$BUILD/classes" "$BUILD/dex" "$BUILD/assets" "$BUILD/res"

echo "== 1. assets (web app + generated config) =="
# Exclude the APK itself: web/ also hosts the download, but it must not be
# bundled inside the APK (would nest the old build into the new one).
rsync -a --exclude 'TruckRoute.apk' "$REPO/web/." "$BUILD/assets/" 2>/dev/null \
  || (cp -r "$REPO/web/." "$BUILD/assets/" && rm -f "$BUILD/assets/TruckRoute.apk")
printf 'window.TRUCKROUTE_API = "%s";\n' "$API_BASE" > "$BUILD/assets/config.js"
find "$BUILD/assets" -type f | wc -l

echo "== 2. launcher icon =="
mkdir -p "$BUILD/res/mipmap-xxxhdpi"
python3 - <<'EOF'
import os
build = "/home/hatch/workspace/.tools/truckroute-build"
try:
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (192, 192), (13, 20, 32, 255))
    d = ImageDraw.Draw(img)
    # amber rounded square
    d.rounded_rectangle([28, 28, 164, 164], radius=36, fill=(245, 166, 35, 255))
    # simple truck glyph
    d.rectangle([48, 76, 118, 128], fill=(13, 20, 32, 255))
    d.rectangle([118, 88, 144, 128], fill=(13, 20, 32, 255))
    d.polygon([(118, 88), (144, 88), (144, 108), (118, 108)], fill=(180, 220, 255, 255))
    for wx in (66, 100, 132):
        d.ellipse([wx - 12, 118, wx + 12, 142], fill=(13, 20, 32, 255))
        d.ellipse([wx - 5, 125, wx + 5, 135], fill=(200, 200, 200, 255))
    img.save(os.path.join(build, "res", "mipmap-xxxhdpi", "ic_launcher.png"))
    print("  icon generated")
except ImportError:
    print("  PIL missing — skipping icon")
EOF

echo "== 3. aapt2 compile + link =="
cp -r "$PROJ/app/src/main/res/." "$BUILD/res/"
"$AAPT2" compile --dir "$BUILD/res" -o "$BUILD/compiled/"
sed "s|@mipmap/ic_launcher|@mipmap/ic_launcher|" "$PROJ/app/src/main/AndroidManifest.xml" > "$BUILD/AndroidManifest.xml"
# aapt2 requires the package attribute (AGP normally injects it)
sed -i 's|<manifest |<manifest package="com.truckroute.app" |' "$BUILD/AndroidManifest.xml"
# reference the icon only if we generated one
if [ ! -f "$BUILD/res/mipmap-xxxhdpi/ic_launcher.png" ]; then
  sed -i 's/ android:icon="@mipmap\/ic_launcher"//' "$BUILD/AndroidManifest.xml" || true
else
  sed -i 's|<application|<application android:icon="@mipmap/ic_launcher"|' "$BUILD/AndroidManifest.xml"
fi
"$AAPT2" link -o "$BUILD/base.apk" \
  -I "$ANDROID_JAR" \
  --manifest "$BUILD/AndroidManifest.xml" \
  --min-sdk-version 26 --target-sdk-version 36 \
  --version-code "$VERCODE" --version-name "$VERNAME" \
  --java "$BUILD/gen" \
  -A "$BUILD/assets" \
  $(find "$BUILD/compiled" -name "*.flat")

echo "== 4. javac =="
find "$PROJ/app/src/main/java" -name "*.java" > "$BUILD/sources.txt"
find "$BUILD/gen" -name "R.java" >> "$BUILD/sources.txt"
javac --release 17 -nowarn -cp "$ANDROID_JAR" -d "$BUILD/classes" @"$BUILD/sources.txt"
echo "  classes: $(find "$BUILD/classes" -name '*.class' | wc -l)"

echo "== 5. d8 =="
"$JAVA_HOME/bin/java" -cp "$BT/lib/d8.jar" com.android.tools.r8.D8 \
  --lib "$ANDROID_JAR" --min-api 26 \
  --output "$BUILD/dex" \
  $(find "$BUILD/classes" -name "*.class")
ls "$BUILD/dex"

echo "== 6. assemble + align + sign =="
cp "$BUILD/base.apk" "$BUILD/app.apk"
python3 - "$BUILD" <<'EOF'
import sys, zipfile, glob
build = sys.argv[1]
dexes = sorted(glob.glob(build + "/dex/classes*.dex"))
assert dexes, "no dex files produced"
with zipfile.ZipFile(build + "/app.apk", "a", zipfile.ZIP_DEFLATED) as z:
    for d in dexes:
        name = "classes.dex" if d.endswith("classes.dex") else d.split("/")[-1]
        z.write(d, name)
        print("  added", name)
# sanity: MainActivity must be in the dex payload
names = []
with zipfile.ZipFile(build + "/app.apk") as z:
    names = z.namelist()
assert any(n == "classes.dex" for n in names), "classes.dex missing from APK"
EOF
"$BT/zipalign" -f -p 4 "$BUILD/app.apk" "$BUILD/app-aligned.apk"
KS=/home/hatch/workspace/.tools/truckroute-build/debug.keystore
if [ ! -f "$KS" ]; then
  keytool -genkeypair -keystore "$KS" -alias truckroute \
    -keyalg RSA -keysize 2048 -validity 10950 \
    -storepass truckroute -keypass truckroute \
    -dname "CN=TruckRoute,O=TruckRoute,C=US" 2>/dev/null
fi
"$JAVA_HOME/bin/java" -jar "$BT/lib/apksigner.jar" sign --ks "$KS" \
  --ks-pass pass:truckroute --key-pass pass:truckroute \
  --out "$BUILD/TruckRoute.apk" "$BUILD/app-aligned.apk"
"$JAVA_HOME/bin/java" -jar "$BT/lib/apksigner.jar" verify "$BUILD/TruckRoute.apk"
ls -la "$BUILD/TruckRoute.apk"
echo "APK -> $BUILD/TruckRoute.apk"
