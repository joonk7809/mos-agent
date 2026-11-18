plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "dev.mos.agent"
    compileSdk = 35

    defaultConfig {
        applicationId = "dev.mos.agent"
        minSdk = 30          // AccessibilityService.takeScreenshot and ACTION_IME_ENTER need API 30
        targetSdk = 35
        versionCode = 1
        versionName = "0.1.0"
    }

    // MOS ROM builds (rom/build.sh) sign with the MOS app key, whose certificate the
    // ROM's bridge pins. Without MOS_KEYSTORE, release stays debug-signed for sideloading.
    val mosKeystore = System.getenv("MOS_KEYSTORE")
    if (mosKeystore != null) {
        val pass = System.getenv("MOS_KEYSTORE_PASSWORD_FILE")?.let { file(it).readText().trim() }
            ?: error("MOS_KEYSTORE set without MOS_KEYSTORE_PASSWORD_FILE")
        signingConfigs.create("mos") {
            storeFile = file(mosKeystore)
            storePassword = pass
            keyAlias = "mos"
            keyPassword = pass
        }
    }
    // Emulator ROM images (rom/build.sh sdk_phone_*) preinstall a debug app for the eval driver.
    // It is signed with a separate emulator-only key (MOS_DEBUG_KEYSTORE), never the phone key:
    // a debuggable app signed with the phone key could replace the app on a real MOS phone.
    val mosDebugKeystore = System.getenv("MOS_DEBUG_KEYSTORE")
    if (mosDebugKeystore != null) {
        require(mosDebugKeystore != mosKeystore) { "MOS_DEBUG_KEYSTORE must not be the phone key (MOS_KEYSTORE)" }
        val pass = System.getenv("MOS_DEBUG_KEYSTORE_PASSWORD_FILE")?.let { file(it).readText().trim() }
            ?: error("MOS_DEBUG_KEYSTORE set without MOS_DEBUG_KEYSTORE_PASSWORD_FILE")
        signingConfigs.create("mosDebug") {
            storeFile = file(mosDebugKeystore)
            storePassword = pass
            keyAlias = "mos"
            keyPassword = pass
        }
    }

    buildFeatures {
        aidl = true // IMosBridge, the ROM's privileged input bridge
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = signingConfigs.findByName("mos") ?: signingConfigs.getByName("debug")
        }
        debug {
            signingConfig = signingConfigs.findByName("mosDebug") ?: signingConfigs.getByName("debug")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

kotlin {
    compilerOptions { jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17) }
}

dependencies {
    implementation(project(":core"))
}
