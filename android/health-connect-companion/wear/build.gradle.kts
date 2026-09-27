plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "ai.clinicianassist.personalstate"
    compileSdk = 36

    defaultConfig {
        applicationId = "ai.clinicianassist.personalstate"
        minSdk = 30
        targetSdk = 36
        versionCode = 40101
        versionName = "0.4.1"
    }

    val releaseKeystore = System.getenv("PERSONAL_STATE_ANDROID_KEYSTORE")
    signingConfigs {
        if (!releaseKeystore.isNullOrBlank()) {
            create("release") {
                storeFile = file(releaseKeystore)
                storePassword = System.getenv("PERSONAL_STATE_ANDROID_STORE_PASSWORD")
                keyAlias = System.getenv("PERSONAL_STATE_ANDROID_KEY_ALIAS") ?: "personal-state"
                keyPassword = System.getenv("PERSONAL_STATE_ANDROID_KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            if (!releaseKeystore.isNullOrBlank()) {
                signingConfig = signingConfigs.getByName("release")
            }
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
}

dependencies {
    implementation("androidx.core:core-ktx:1.15.0")
    implementation("androidx.health:health-services-client:1.1.0")
    implementation("com.google.android.gms:play-services-wearable:20.0.1")
    implementation("com.google.guava:guava:31.0.1-android")
}
