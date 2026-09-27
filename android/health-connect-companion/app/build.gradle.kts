plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("kotlin-parcelize")
}

val samsungSdkAar = providers.environmentVariable("SAMSUNG_HEALTH_DATA_SDK_AAR")
    .orElse(providers.gradleProperty("samsungHealthDataSdkAar"))
    .orNull
    ?.let(::file)
    ?.takeIf { it.isFile }

android {
    namespace = "ai.clinicianassist.personalstate"
    compileSdk = 36

    defaultConfig {
        applicationId = "ai.clinicianassist.personalstate"
        minSdk = if (samsungSdkAar == null) 28 else 29
        targetSdk = 35
        versionCode = 40000
        versionName = "0.4.0"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
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
    buildFeatures { viewBinding = true }

    if (samsungSdkAar != null) {
        sourceSets.getByName("main").java.srcDir("src/samsungSdk/java")
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.15.0")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("androidx.activity:activity-ktx:1.10.0")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.7")
    implementation("androidx.work:work-runtime-ktx:2.10.0")
    implementation("androidx.health.connect:connect-client:1.1.0")
    implementation("com.google.android.material:material:1.12.0")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.9.0")
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.7.3")
    implementation("com.google.android.gms:play-services-wearable:20.0.1")
    if (samsungSdkAar != null) {
        implementation(files(samsungSdkAar))
        implementation("com.google.code.gson:gson:2.11.0")
    }

    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20240303")
    androidTestImplementation("androidx.test.ext:junit:1.2.1")
    androidTestImplementation("androidx.test.espresso:espresso-core:3.6.1")
}
