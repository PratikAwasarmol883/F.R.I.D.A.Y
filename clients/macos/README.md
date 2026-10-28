# FRIDAY voice client (macOS)

Push-to-talk voice client for the FRIDAY API.

```bash
pip install -e ".[mac]"          # httpx plus sounddevice and keyring (see pyproject.toml)
python -m friday_client login --server https://your-server   # stores the device token in the Keychain
python -m friday_client --server https://your-server          # Enter = talk, m = mute, q = quit
```

- The status line always shows when audio is being recorded or sent. While **muted** nothing is captured
  and nothing is sent; the mute is enforced in the client core, not just in the display.
- The device token is stored in the macOS Keychain (or read from `FRIDAY_TOKEN`) and is never printed or logged.
- If the server cannot synthesize speech, or playback fails, the answer is spoken locally with `say`.
- **Verified by automated tests:** the state machine, mute behavior, every failure path, the API client, WAV
  encoding, silence detection, token storage with fakes.
- **Not verified here (needs a Mac with devices):** `SoundDeviceMicrophone`, `AfplayPlayer`, `SayLocalSpeaker`,
  and the Keychain through `keyring`.
- A "Hey Friday" wake word needs a custom-trained model that is not included. Implement `Trigger.wait` with a
  detector of your choice; it must not capture audio while `muted=True`.
