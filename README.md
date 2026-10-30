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

Settings are saved to `%APPDATA%\NTELightningCapture\config.json`.

## Run from source

Install Python 3.11 or newer, then double-click `run_from_source.bat`. `build.bat` builds the .exe.
