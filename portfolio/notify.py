"""
notify.py — Push a report digest to your phone.

    python portfolio/notify.py --text "..."                 send a message
    python portfolio/notify.py --file reports/x.md          attach a file
    python portfolio/notify.py --text "..." --file x.md     both
    python portfolio/notify.py --test                       verify setup

Telegram is the default channel. It works from Windows, Mac, or a scheduled job,
costs nothing, and can attach the full report as a file instead of truncating it.

Setup, about three minutes:
  1. In Telegram, message @BotFather and send /newbot. Follow the prompts.
  2. It gives you a token like 8123456789:AAF... Put it in .env as
     TELEGRAM_BOT_TOKEN.
  3. Send your new bot any message ("hi" is fine). A bot cannot start a
     conversation with you, so this step is required.
  4. Run: python portfolio/notify.py --whoami
     It reads your chat id off that message. Put it in .env as TELEGRAM_CHAT_ID.
  5. Run: python portfolio/notify.py --test

iMessage is stubbed. It requires macOS and AppleScript against Messages.app, so
it cannot work from a Windows machine. The channel interface is here so it is a
short addition if this ever runs from a Mac.
"""

from __future__ import annotations

import argparse
import os
import platform
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

for _env in [
    Path(__file__).parent / ".env",
    Path(__file__).parent.parent / ".env",
    Path(__file__).parent.parent.parent / ".env",
]:
    if _env.exists():
        load_dotenv(_env)
        break

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"
TELEGRAM_MAX_CHARS = 4096
TIMEOUT = 30


class NotifyError(RuntimeError):
    pass


class Channel:
    """Interface every delivery channel implements."""

    name = "base"

    def send_text(self, text: str) -> None:
        raise NotImplementedError

    def send_file(self, path: Path, caption: str | None = None) -> None:
        raise NotImplementedError


class Telegram(Channel):
    name = "telegram"

    def __init__(self) -> None:
        self.token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID")
        if not self.token:
            raise NotifyError(
                "TELEGRAM_BOT_TOKEN is not set in .env. See the setup steps at the "
                "top of notify.py."
            )
        if not self.chat_id:
            raise NotifyError(
                "TELEGRAM_CHAT_ID is not set in .env. Message your bot once, then "
                "run: python portfolio/notify.py --whoami"
            )

    def _call(self, method: str, **kwargs) -> dict:
        url = TELEGRAM_API.format(token=self.token, method=method)
        try:
            resp = requests.post(url, timeout=TIMEOUT, **kwargs)
        except requests.RequestException as exc:
            raise NotifyError(f"Telegram request failed: {exc}") from exc

        try:
            body = resp.json()
        except ValueError:
            raise NotifyError(f"Telegram returned non-JSON (HTTP {resp.status_code})")

        if not body.get("ok"):
            raise NotifyError(
                f"Telegram rejected the request: {body.get('description', body)}"
            )
        return body["result"]

    def send_text(self, text: str) -> None:
        """Send a message, splitting rather than truncating if it is long.

        Telegram caps a message at 4096 characters. Splitting on paragraph
        boundaries keeps the digest readable when it runs over.
        """
        for chunk in _split(text, TELEGRAM_MAX_CHARS):
            self._call("sendMessage", data={
                "chat_id": self.chat_id,
                "text": chunk,
                "disable_web_page_preview": True,
            })

    def send_file(self, path: Path, caption: str | None = None) -> None:
        if not path.exists():
            raise NotifyError(f"{path} does not exist")
        with open(path, "rb") as f:
            self._call(
                "sendDocument",
                data={
                    "chat_id": self.chat_id,
                    # Telegram caps captions at 1024 chars, shorter than messages.
                    "caption": (caption or "")[:1024],
                },
                files={"document": (path.name, f)},
            )


class IMessage(Channel):
    """macOS only. Not implemented, and cannot work on Windows."""

    name = "imessage"

    def __init__(self) -> None:
        if platform.system() != "Darwin":
            raise NotifyError(
                "iMessage delivery requires macOS. This machine reports "
                f"{platform.system()}. Use the telegram channel instead."
            )
        raise NotifyError(
            "The iMessage channel is a stub. On a Mac it would shell out to "
            "osascript against Messages.app. Telegram is the supported channel."
        )


CHANNELS = {"telegram": Telegram, "imessage": IMessage}


def _split(text: str, limit: int) -> list[str]:
    """Break text into chunks under `limit`, preferring paragraph boundaries."""
    if len(text) <= limit:
        return [text]

    chunks, current = [], ""
    for para in text.split("\n\n"):
        candidate = f"{current}\n\n{para}" if current else para
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
        # A single paragraph over the limit gets hard-split.
        while len(para) > limit:
            chunks.append(para[:limit])
            para = para[limit:]
        current = para
    if current:
        chunks.append(current)
    return chunks


def get_channel(name: str | None = None) -> Channel:
    name = name or os.getenv("NOTIFY_CHANNEL", "telegram")
    if name not in CHANNELS:
        raise NotifyError(f"Unknown channel '{name}'. Options: {', '.join(CHANNELS)}")
    return CHANNELS[name]()


def whoami() -> int:
    """Read the chat id off the most recent message sent to the bot."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        print("\n  TELEGRAM_BOT_TOKEN is not set in .env.\n")
        return 1

    url = TELEGRAM_API.format(token=token, method="getUpdates")
    try:
        body = requests.get(url, timeout=TIMEOUT).json()
    except (requests.RequestException, ValueError) as exc:
        print(f"\n  Could not reach Telegram: {exc}\n")
        return 1

    if not body.get("ok"):
        print(f"\n  Telegram said: {body.get('description')}\n")
        return 1

    chats = {}
    for update in body.get("result", []):
        msg = update.get("message") or update.get("channel_post") or {}
        chat = msg.get("chat") or {}
        if chat.get("id"):
            name = chat.get("username") or chat.get("first_name") or chat.get("title") or ""
            chats[chat["id"]] = name

    if not chats:
        print(
            "\n  No messages found. Send your bot any message in Telegram first, "
            "then run this again.\n"
            "  (Telegram only exposes chats that have messaged the bot, and it "
            "drops updates after 24 hours.)\n"
        )
        return 1

    print("\n  Chats that have messaged this bot:")
    for chat_id, name in chats.items():
        print(f"    TELEGRAM_CHAT_ID={chat_id}    {name}")
    print("\n  Put the right one in your .env.\n")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Send a portfolio notification.")
    ap.add_argument("--text", help="Message body")
    ap.add_argument("--file", type=Path, help="File to attach")
    ap.add_argument("--caption", help="Caption for the attached file")
    ap.add_argument("--channel", choices=list(CHANNELS), help="Delivery channel")
    ap.add_argument("--test", action="store_true", help="Send a test message")
    ap.add_argument("--whoami", action="store_true", help="Look up your Telegram chat id")
    args = ap.parse_args()

    if args.whoami:
        return whoami()

    if not (args.text or args.file or args.test):
        ap.error("give me something to send: --text, --file, or --test")

    try:
        channel = get_channel(args.channel)

        if args.test:
            channel.send_text(
                "Portfolio agent test message. If you are reading this on your "
                "phone, notifications are wired up correctly."
            )
            print(f"\n  Test message sent via {channel.name}. Check your phone.\n")
            return 0

        if args.text:
            channel.send_text(args.text)
        if args.file:
            channel.send_file(args.file, args.caption or args.text)

    except NotifyError as exc:
        print(f"\n  {exc}\n")
        return 1

    print(f"  Sent via {channel.name}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
