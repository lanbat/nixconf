# pkgs/android-presence/default.nix
#
# The android-presence Android app as an APK, built in Nix without Gradle.  The
# app is a Bluetooth room sensor that the assistant router treats like a Pi
# room-presence scanner (README.md in this directory); android-provision
# installs the APK it produces here over ADB.
#
# Gradle's on-disk cache is not reproducible (its metadata embeds values that
# change run to run), so instead the APK is assembled by hand from the Android
# SDK's own tools -- aapt2 (manifest + resources), javac, d8, zipalign and
# apksigner.  Every input is content-addressed: the SDK, the JDK, and the one
# runtime dependency (OkHttp) pinned below.  Building is heavy (the SDK is
# ~1 GB) but sandboxed and reproducible, and needs no network.
{
  lib,
  stdenv,
  fetchurl,
  zip,
  jdk17,
  androidenv,
}:

let
  # The SDK the app compiles against (compileSdk 34; nixpkgs' androidenv ships
  # build-tools 37.0.0, which is what aapt2/d8/apksigner run from here).
  androidComposition = androidenv.composeAndroidPackages {
    buildToolsVersions = [ "37.0.0" ];
    platformVersions = [ "34" ];
    includeEmulator = false;
    includeNDK = false;
    includeCmake = false;
    includeSystemImages = false;
    includeSources = false;
  };
  sdk = "${androidComposition.androidsdk}/libexec/android-sdk";
  buildTools = "${sdk}/build-tools/37.0.0";
  androidJar = "${sdk}/platforms/android-34/android.jar";

  # OkHttp (the WebSocket client) and its runtime dependencies, pinned from
  # Maven Central.  Kotlin-stdlib-jdk7/8 are what okhttp 4.12.0 declares.
  okhttp = fetchurl {
    url = "https://repo1.maven.org/maven2/com/squareup/okhttp3/okhttp/4.12.0/okhttp-4.12.0.jar";
    sha256 = "1q7lsn8p2xhrq17klmxlag2apksx3gq3wkaswnks7dsbn60h01di";
  };
  okio = fetchurl {
    url = "https://repo1.maven.org/maven2/com/squareup/okio/okio-jvm/3.6.0/okio-jvm-3.6.0.jar";
    sha256 = "04l4l8yp3jrpjdbh0d8dvagjcpmwk15m03pd4zljlhpw6q3kym37";
  };
  kotlinStdlib = fetchurl {
    url = "https://repo1.maven.org/maven2/org/jetbrains/kotlin/kotlin-stdlib/1.8.21/kotlin-stdlib-1.8.21.jar";
    sha256 = "0am5dra5sjdsrz46jnp19292r28b1f71sgxnbvzdqv4pmk8iqah4";
  };
  kotlinStdlibJdk7 = fetchurl {
    url = "https://repo1.maven.org/maven2/org/jetbrains/kotlin/kotlin-stdlib-jdk7/1.8.21/kotlin-stdlib-jdk7-1.8.21.jar";
    sha256 = "11lx0f4p11lpsl7h0c3pqzwhgnff5cj2hzb7j06vvphi1vdlil9k";
  };
  kotlinStdlibJdk8 = fetchurl {
    url = "https://repo1.maven.org/maven2/org/jetbrains/kotlin/kotlin-stdlib-jdk8/1.8.21/kotlin-stdlib-jdk8-1.8.21.jar";
    sha256 = "1mxpn8f8z67na4k49xf7pg9g8i6s4xpsm13rqpk6xw3l02im5drx";
  };
in
stdenv.mkDerivation {
  pname = "android-presence";
  version = "1.0";

  src = ./.;
  nativeBuildInputs = [
    jdk17
    zip
  ];

  buildPhase = ''
    BT="${buildTools}"

    # 1. Resources -> compiled flat archive.
    "$BT/aapt2" compile --dir app/src/main/res -o res.zip

    # The source manifest leaves the package to AGP's `namespace`; aapt2 wants
    # it in the manifest, so inject it (the build's copy, not the repo's).
    sed -i 's|xmlns:android="http://schemas.android.com/apk/res/android">|xmlns:android="http://schemas.android.com/apk/res/android" package="com.lanbat.presence">|' \
      app/src/main/AndroidManifest.xml

    # 2. Manifest + resources -> an APK skeleton (no dex yet).
    "$BT/aapt2" link -o base.apk -I "${androidJar}" \
      --manifest app/src/main/AndroidManifest.xml \
      -R res.zip --auto-add-overlay \
      --min-sdk-version 26 --target-sdk-version 34 \
      --version-code 1 --version-name 1.0

    # 3. Java -> class files (the app targets Java 8, i.e. API 26).  OkHttp's
    # public API references okio and Kotlin types, so they are on the classpath
    # too (javac resolves every overload of WebSocket.send, for example).
    mkdir -p classes
    "${jdk17}/bin/javac" --release 8 -encoding UTF-8 \
      -classpath "${androidJar}:${okhttp}:${okio}:${kotlinStdlib}" \
      -d classes $(find app/src/main/java -name '*.java')

    # 4. Classes + runtime jars -> classes.dex.
    mkdir -p dexout
    "$BT/d8" --lib "${androidJar}" --min-api 26 --output dexout \
      $(find classes -name '*.class') \
      "${okhttp}" "${okio}" "${kotlinStdlib}" "${kotlinStdlibJdk7}" "${kotlinStdlibJdk8}"

    # 5. Add classes.dex to the APK.
    "${zip}/bin/zip" -j base.apk dexout/classes.dex

    # 6. Align and sign (a fixed debug keystore keeps the output reproducible).
    "$BT/zipalign" -f 4 base.apk aligned.apk
    "$BT/apksigner" sign --ks debug.keystore --ks-pass pass:android --key-pass pass:android \
      --out signed.apk aligned.apk
  '';

  installPhase = ''
    mkdir -p "$out"
    install -Dm644 signed.apk "$out/android-presence.apk"
  '';

  meta = with lib; {
    description = "Android TV Bluetooth room sensor app for the assistant router";
    license = licenses.mit;
    mainProgram = "android-presence";
  };
}
