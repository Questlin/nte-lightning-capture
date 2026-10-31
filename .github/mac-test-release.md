> [!WARNING]
> **Using this tool very likely breaks Neverness to Everness's terms of use.** NTE does not allow third-party programs or automation, and players report bans for using them. **Your account could be suspended or banned. Use it at your own risk.** This is an unofficial fan project, not made, endorsed or supported by the developers or publishers of NTE.

**Test build for Mac.** For Apple Silicon Macs (M1 or later) with macOS 14 or newer. It has not been tried with the game much yet. The Windows app is in the latest regular release.

1. Download `NTELightningCapture-mac.zip` and unzip it. The folder holds the app and **Read Me First.txt** with these steps in more detail (also attached below as `Read-Me-Mac.txt`). Drag **NTE Lightning Capture.app** to Applications.
2. Open it. macOS says it can't verify the app (it isn't signed with an Apple Developer ID). Click **Done**, then go to System Settings > Privacy & Security, click **Open Anyway** next to the message about NTE Lightning Capture, and confirm.
3. Allow **Screen & System Audio Recording**, **Accessibility** and **Input Monitoring** for the app in System Settings > Privacy & Security, then quit and reopen it. The app's log lists any that are still missing.
4. Select regions, switch to the game, and press **fn+F6** to arm.

Please check: (1) **Select regions on screen…** shows a screenshot of the game, (2) **Start watching** with **Debug options** ticked shows the live signal moving and 30 fps or more, (3) **Test photo key** takes a photo in NTE (if not, try **Send the key as: Send to game only**), and (4) the arm hotkey works from inside the game.

Full instructions are in the [README on the mac-test branch](https://github.com/Questlin/nte-lightning-capture/blob/mac-test/README.md#mac-test-version).
