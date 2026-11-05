# RobustNotes Mobile

`android/` is the native Android starter, kept separate from the PySide desktop application. The current Android MVP creates, edits, and deletes plain-text notes stored in the app's private files directory. It does not upload notes or share the desktop profile folder.

## Open and run

Install Android Studio with Android SDK Platform 35 and a JDK supported by the project. From PowerShell:

```powershell
Set-Location .\mobile\android
.\gradlew.bat assembleDebug
```

The Gradle 8.9 wrapper downloads its own Gradle distribution. Android Studio or the Android SDK command-line tools must still provide the Android SDK. If Gradle cannot find the SDK, set `sdk.dir` in the machine-local `local.properties` file (do not commit that file).

The debug APK is written to `app\build\outputs\apk\debug\app-debug.apk`.

The Android project is an initial shell, not feature parity with desktop. Planned native work includes stylus notebook pages, PDF import/export, encrypted backup, cross-device transfer, and native handwriting/OCR workflows.
