# NTE Lightning Capture

> [!WARNING]
> **Using this tool very likely breaks Neverness to Everness's terms of use.** NTE does not allow third-party programs or automation, and players report bans for using them. This app watches your screen and presses the game's photo key for you, which counts as automation. **Your account could be suspended or banned. Use it at your own risk.**
>
> This is an unofficial fan project. It is not made, endorsed or supported by the developers or publishers of NTE.

A small Windows app that watches part of your screen while NTE is in photo mode, and takes a photo the moment lightning strikes there.

It only reads what is on screen and presses one key. It does not read or change the game's memory or files, and it does not connect to the internet.

## Download

Get `NTELightningCapture.exe` from the [Releases](../../releases) page. There is nothing to install.

- Windows may show **"Windows protected your PC"** because the app isn't signed. Click **More info**, then **Run anyway**.
- Windows also asks to let the app make changes each time it starts. Click **Yes**. NTE runs as administrator, and Windows ignores key presses sent to it by apps that aren't.

## How to use it

1. **Set a region.** With NTE running, open NTE Lightning Capture and click **Select regions on screen…**. The app shows a screenshot of your screen. Drag a rectangle over the sky where lightning appears, keeping it away from your character, the screen edges and the on-screen controls. Press **Enter** to save.
2. **Switch to the game.**
3. **Set up photo mode.** Open photo mode and set the weather to **rain**.
4. **Arm the tool.** Press **F6**. You'll hear a rising beep. When lightning strikes in your region, the app presses **F** to take a photo. Press **F6** again to disarm (falling beep).

By default the app disarms itself after one photo. To keep going, tick **Debug options** and set **Stop after this many photos** to a higher number, or 0 for no limit.

If NTE's photo key isn't **F**, change **Game's photo key/button** in the app before arming. The app only watches while the game window is in front, and pauses when you switch away.

Settings are saved to `%APPDATA%\NTELightningCapture\config.json` on Windows.

## Mac (test version)

A test build for Apple Silicon Macs (M1 or later, macOS 14 or newer, the same Macs NTE's Mac client runs on) is on the [Releases](../../releases) page as a **pre-release**: `NTELightningCapture-mac.zip`. It has not been tried with the game much yet.

1. **Install.** Unzip it and drag **NTE Lightning Capture.app** to Applications.
2. **First launch.** The app isn't signed with an Apple Developer ID, so macOS says it can't verify it. Click **Done**, open System Settings > Privacy & Security, scroll down and click **Open Anyway** next to the message about NTE Lightning Capture, then confirm. (Or in Terminal: `xattr -dr com.apple.quarantine "/Applications/NTE Lightning Capture.app"`.)
3. **Permissions.** macOS asks for three. Allow each one in System Settings > Privacy & Security, then quit and reopen the app:
   - **Screen & System Audio Recording**, so the app can see the game.
   - **Accessibility**, so it can press the photo key.
   - **Input Monitoring**, so the arm hotkey works while the game is in front (the **Arm** button works without it).

   The app's log lists any that are still missing. Each new version needs them again: remove the old entry with the minus button in each list, then allow the new one.
4. **Use it** as described above. On a Mac keyboard, press **fn+F6** to arm, unless your F keys are set to work as standard function keys. To delete a region while selecting, right-click or Control-click it.

**Please check, and report back:** (1) **Select regions on screen…** shows a screenshot of the game, (2) **Start watching** with **Debug options** ticked shows the live signal moving and 30 fps or more in the status line, (3) **Test photo key** takes a photo in NTE (if not, try **Send the key as: Send to game only**), and (4) the arm hotkey works from inside the game.

Mac settings are saved to `~/Library/Application Support/NTELightningCapture/config.json`.

## Run from source

Install Python 3.11 or newer, then double-click `run_from_source.bat` (Windows) or `run_from_source.command` (Mac). `build.bat` builds the .exe and `build_mac.sh` the Mac app.
