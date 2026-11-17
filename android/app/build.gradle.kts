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

    buildFeatures {
        aidl = true // IMosBridge, the ROM's privileged input bridge
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = signingConfigs.findByName("mos") ?: signingConfigs.getByName("debug")
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
