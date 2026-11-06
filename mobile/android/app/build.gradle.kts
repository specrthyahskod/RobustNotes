plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

val uploadKeystore = providers.environmentVariable("ROBUSTNOTES_UPLOAD_KEYSTORE").orNull
val uploadStorePassword = providers.environmentVariable("ROBUSTNOTES_UPLOAD_STORE_PASSWORD").orNull
val uploadKeyAlias = providers.environmentVariable("ROBUSTNOTES_UPLOAD_KEY_ALIAS").orNull
val uploadKeyPassword = providers.environmentVariable("ROBUSTNOTES_UPLOAD_KEY_PASSWORD").orNull
val hasUploadSigning = listOf(uploadKeystore, uploadStorePassword, uploadKeyAlias, uploadKeyPassword)
    .all { !it.isNullOrBlank() }

android {
    namespace = "com.robustnotes.mobile"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.robustnotes.mobile"
        minSdk = 26
        targetSdk = 36
        versionCode = 1
        versionName = "0.1.0"
    }

    buildFeatures { compose = true }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }

    if (hasUploadSigning) {
        signingConfigs {
            create("release") {
                storeFile = file(uploadKeystore!!)
                storePassword = uploadStorePassword
                keyAlias = uploadKeyAlias
                keyPassword = uploadKeyPassword
            }
        }
    }

    buildTypes {
        release {
            if (hasUploadSigning) signingConfig = signingConfigs.getByName("release")
        }
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.15.0")
    implementation("androidx.activity:activity-compose:1.10.0")
    implementation(platform("androidx.compose:compose-bom:2025.01.00"))
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.7")
    debugImplementation("androidx.compose.ui:ui-tooling")
}

tasks.configureEach {
    if (name == "bundleRelease" || name == "assembleRelease") {
        doFirst {
            check(hasUploadSigning) {
                "Release signing is missing. Set ROBUSTNOTES_UPLOAD_KEYSTORE, ROBUSTNOTES_UPLOAD_STORE_PASSWORD, ROBUSTNOTES_UPLOAD_KEY_ALIAS, and ROBUSTNOTES_UPLOAD_KEY_PASSWORD."
            }
        }
    }
}
