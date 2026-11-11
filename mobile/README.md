# RobustNotes for Android

The Android app is a native, offline-first text notes app. It has note creation and editing, title/body search, deletion confirmation, and atomic local persistence in Android's app-private storage. It uses network access only for Google Play's app-update flow and does not request broad storage permissions. Android backup is disabled. Notes are not synced to the desktop app or another device.

This is an initial Android release, not feature parity with the Windows desktop app. It does not currently include handwritten notebooks, stylus drawing, PDF annotation, OCR, AI chat, or whiteboards. The website and desktop feature list should not imply that those features are already available on Android.

The app checks Google Play for updates when it opens and also has an **Updates** action in the top bar. Updates use Google Play's flexible in-app update flow, so the user can keep using notes while an update downloads, then choose when to restart. In-app updates work only for installs distributed by Google Play (including its internal testing tracks) and signed for the same Play app. A manually installed APK must be updated by installing a newer APK; the app does not request permission to silently install packages or download installers from arbitrary sources. Note content is not sent as part of an update check.

## Build a debug APK

Install Android Studio or Android command-line tools with Android SDK Platform 36 and a supported JDK (JDK 17 recommended). From PowerShell:

```powershell
Set-Location .\mobile\android
.\gradlew.bat assembleDebug
```

The Gradle 8.11.1 wrapper downloads its own Gradle distribution. Set `ANDROID_HOME` or add a machine-local `local.properties` file containing `sdk.dir=C:\\Users\\<you>\\AppData\\Local\\Android\\Sdk` if Gradle cannot find your SDK. Do not commit `local.properties`.

Debug APK: `app\build\outputs\apk\debug\app-debug.apk`.

## Prepare a Play Store bundle

Google Play requires new apps to target Android 16 (API 36) as of August 31, 2026. This project targets API 36. See [Google Play's target API requirements](https://support.google.com/googleplay/android-developer/answer/11926878).

Create and keep a private upload keystore outside the repository. Never commit the keystore or passwords. From PowerShell, create a key once (keep the file and passwords in a secure backup):

```powershell
keytool -genkeypair -v -keystore "$env:USERPROFILE\robustnotes-upload.jks" -keyalg RSA -keysize 2048 -validity 10000 -alias robustnotes-upload
```

Before building, set these four environment variables in the same PowerShell session: `ROBUSTNOTES_UPLOAD_KEYSTORE`, `ROBUSTNOTES_UPLOAD_STORE_PASSWORD`, `ROBUSTNOTES_UPLOAD_KEY_ALIAS`, and `ROBUSTNOTES_UPLOAD_KEY_PASSWORD`. The release task fails if signing is not configured. Then build from `mobile\android`:

The policy page to enter in Play Console is `https://robustnotes.com/privacy.html`; it is also linked in the Android app's top bar. Verify the public URL is live over HTTPS before submitting. Complete the Play Console Data safety form to match the release artifact and its dependencies, including Google Play's update SDK.

```powershell
.\gradlew.bat bundleRelease
```

The AAB is written to `app\build\outputs\bundle\release\app-release.aab`. Confirm it is signed with the intended upload key before uploading it. Set the Play Console app access, content rating, Data safety, privacy policy, store listing, screenshots, and any account-specific closed-testing requirements there. Notes remain local; the only new network access is for Play-managed app updates. Review the generated bundle and Play Console declarations before publishing.

For each Play release, increment `versionCode` and update `versionName` in `app\build.gradle.kts`, then build and upload a newly signed AAB. The in-app updater will offer that release after Google Play makes it available to the account/device. Test the flow with Play internal testing or internal app sharing; installing a debug APK directly does not emulate a Play-distributed install.
