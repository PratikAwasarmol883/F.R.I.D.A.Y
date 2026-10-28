"""`python -m friday_client` — talk to FRIDAY from the terminal with push-to-talk."""

import argparse
import getpass
import logging
import select
import sys
from urllib.parse import urlsplit

from friday_client import __version__
from friday_client.api import FridayApi, validate_base_url
from friday_client.audio import AfplayPlayer, SayLocalSpeaker, SoundDeviceMicrophone
from friday_client.core import State, TriggerEvent, VoiceClient
from friday_client.tokens import ChainTokenStore, EnvTokenStore, KeyringTokenStore

_ICONS = {
    State.IDLE: "○ ready (Enter to talk, m to mute, q to quit)",
    State.LISTENING: "● LISTENING — recording is active",
    State.THINKING: "… thinking (audio sent to the server)",
    State.SPEAKING: "♪ speaking",
    State.MUTED: "✕ MUTED — nothing is recorded or sent (m to unmute)",
    State.ERROR: "! error",
}


def stderr_indicator(state: State, detail: str) -> None:
    line = _ICONS[state] + (f": {detail}" if detail and state is State.ERROR else "")
    print(
        f"\r\033[K{line}",
        end="\n" if state in {State.ERROR, State.MUTED} else "",
        file=sys.stderr,
        flush=True,
    )


class KeyboardTrigger:
    """Push-to-talk: Enter talks, `m` toggles mute, `q` quits. Never touches the microphone."""

    def wait(self, timeout: float, *, muted: bool) -> TriggerEvent:
        if not sys.stdin.isatty():
            return TriggerEvent.NONE
        ready, _, _ = select.select([sys.stdin], [], [], timeout)
        if not ready:
            return TriggerEvent.NONE
        line = sys.stdin.readline().strip().lower()
        return {"m": TriggerEvent.TOGGLE_MUTE, "q": TriggerEvent.QUIT}.get(
            line, TriggerEvent.NONE if muted else TriggerEvent.TALK
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="friday-client", description="FRIDAY voice client")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command")
    for name in ("run", "login"):
        command = sub.add_parser(name)
        command.add_argument("--server", default="http://127.0.0.1:8000")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    command = args.command or "run"
    server = getattr(args, "server", "http://127.0.0.1:8000")
    try:
        server = validate_base_url(server)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    account = urlsplit(server).netloc
    keyring_store = KeyringTokenStore(account)

    if command == "login":
        token = getpass.getpass("Device token (input hidden): ").strip()
        if not token:
            print("error: no token entered", file=sys.stderr)
            return 2
        keyring_store.set(token)
        print("Token saved to the macOS Keychain.", file=sys.stderr)
        return 0

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    api = FridayApi(server, ChainTokenStore(EnvTokenStore(), keyring_store).get)
    client = VoiceClient(
        mic=SoundDeviceMicrophone(),
        trigger=KeyboardTrigger(),
        player=AfplayPlayer(),
        local_speaker=SayLocalSpeaker(),
        api=api,
        indicator=stderr_indicator,
    )
    try:
        client.run_forever()
    except KeyboardInterrupt:
        client.stop()
    finally:
        api.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
