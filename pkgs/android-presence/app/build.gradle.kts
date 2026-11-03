plugins {
    id("com.android.application")
}

android {
    namespace = "com.lanbat.presence"
    compileSdk = 34
    // nixpkgs' androidenv ships only build-tools 37.0.0; AGP 8.5.2's default
    // is 34.0.0, so pin the version Nix can provide (see default.nix).
    buildToolsVersion = "37.0.0"

    defaultConfig {
        applicationId = "com.lanbat.presence"
        minSdk = 26
        targetSdk = 34
        versionCode = 1
        versionName = "1.0"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

dependencies {
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    testImplementation("junit:junit:4.13.2")
}
