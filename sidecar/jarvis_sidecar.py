#!/usr/bin/env python3
"""JARVIS sidecar -- eyes and hands on this machine.

JARVIS runs on your server and has no screen, no browser of yours, and no
access to this computer. Run this here and it gains exactly the abilities
you granted when you paired it, and nothing else.

    python3 jarvis_sidecar.py pair https://jarvis.example.com ABCD-1234-EF56
    python3 jarvis_sidecar.py run
    python3 jarvis_sidecar.py repoint https://jarvis.athome.lan

**This program connects OUT. JARVIS never dials in.**

No port is opened on this machine, nothing is forwarded through your
router, and it works from a cafe. It asks the server for work, does what
it is permitted, and brings the answer back. Closing the lid is the off
switch, and there is nothing the server can do about that, because there
is nothing here listening.

**It refuses anything it was not granted.** The server decides what to
ask for; this decides what it is willing to do, from the list it was
paired with. Both have to agree. If the server ever asks for something
outside that list, this says no and says so -- a server that has been
tampered with does not get new hands.

One file, standard library only, so it runs on a Mac, a PC or a Pi with
no install step.
"""
import base64
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

CONFIG = Path(os.environ.get("JARVIS_SIDECAR_CONFIG",
                             Path.home() / ".jarvis-sidecar.json"))

# How long to wait between asking for work. Long enough not to hammer a
# home server, short enough that "take a screenshot" is not annoying.
POLL_SECONDS = 3
# Anything that has not finished by now is killed. A hung command must
# not become a hung sidecar.
ACTION_TIMEOUT = 60
# Enough output to be useful, not enough that one `cat` fills the server.
MAX_OUTPUT = 20_000


def say(*a):
    print("[jarvis]", *a, flush=True)


# --- talking to the server --------------------------------------------------

def call(base: str, path: str, body: dict, token: str = "") -> dict:
    request = urllib.request.Request(
        base.rstrip("/") + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 **({"Authorization": f"Bearer {token}"} if token else {})},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read() or b"{}")


def load() -> dict:
    if not CONFIG.exists():
        say(f"Not paired yet. Run: {sys.argv[0]} pair <server-url> <code>")
        sys.exit(1)
    return json.loads(CONFIG.read_text())


def describe() -> dict:
    return {"machine": f"{platform.system()} {platform.release()}",
            "hostname": socket.gethostname(), "python": platform.python_version()}


# --- what this machine is willing to do ------------------------------------
#
# Each returns a plain dict. Nothing here decides what is ALLOWED -- that
# was decided at pairing and is checked below before any of these run.

def do_screenshot(args: dict) -> dict:
    """A picture of this screen, as a data URI."""
    out = Path("/tmp/jarvis-shot.png")
    if sys.platform == "darwin":
        command = ["screencapture", "-x", str(out)]
    elif sys.platform.startswith("linux"):
        tool = next((t for t in ("gnome-screenshot", "scrot", "import")
                     if shutil.which(t)), None)
        if tool is None:
            raise RuntimeError(
                "No screenshot tool. Install one: apt install gnome-screenshot")
        command = ({"gnome-screenshot": [tool, "-f", str(out)],
                    "scrot": [tool, "-o", str(out)],
                    "import": [tool, "-window", "root", str(out)]})[tool]
    else:
        command = ["powershell", "-NoProfile", "-Command",
                   "Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
                   "$b=[System.Windows.Forms.Screen]::PrimaryScreen.Bounds;"
                   "$i=New-Object Drawing.Bitmap $b.Width,$b.Height;"
                   "[Drawing.Graphics]::FromImage($i).CopyFromScreen("
                   "$b.Location,[Drawing.Point]::Empty,$b.Size);"
                   f"$i.Save('{out}')"]
    subprocess.run(command, check=True, timeout=ACTION_TIMEOUT)
    raw = out.read_bytes()
    out.unlink(missing_ok=True)
    return {"png_base64": base64.b64encode(raw).decode(), "bytes": len(raw)}


def do_files_read(args: dict) -> dict:
    path = Path(str(args.get("path", ""))).expanduser()
    if not path.is_file():
        raise RuntimeError(f"No such file: {path}")
    text = path.read_text(errors="replace")[:MAX_OUTPUT]
    return {"path": str(path), "text": text, "truncated": len(text) >= MAX_OUTPUT}


def do_files_write(args: dict) -> dict:
    path = Path(str(args.get("path", ""))).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(args.get("text", "")))
    return {"path": str(path), "bytes": path.stat().st_size}


def do_terminal(args: dict) -> dict:
    """Run a command. Never through a shell.

    The server sends an argument vector, not a string. A string would be
    a shell command, and a shell command is a place where a semicolon
    means something -- which is exactly the thing that must not be true
    of a channel reaching this machine from somewhere else.
    """
    argv = args.get("argv")
    if not isinstance(argv, list) or not argv or not all(
            isinstance(a, str) for a in argv):
        raise RuntimeError(
            "A command must arrive as a list of strings, never as one "
            "string -- there is no shell here on purpose.")
    done = subprocess.run(argv, capture_output=True, text=True,
                          timeout=ACTION_TIMEOUT, cwd=str(Path.home()))
    return {"argv": argv, "exit_code": done.returncode,
            "stdout": done.stdout[:MAX_OUTPUT], "stderr": done.stderr[:MAX_OUTPUT]}


def do_clipboard(args: dict) -> dict:
    wanted = str(args.get("text", ""))
    if sys.platform == "darwin":
        read, write = ["pbpaste"], ["pbcopy"]
    elif sys.platform.startswith("linux"):
        if not shutil.which("xclip"):
            raise RuntimeError("No xclip. Install it: apt install xclip")
        read = ["xclip", "-o", "-selection", "clipboard"]
        write = ["xclip", "-selection", "clipboard"]
    else:
        read, write = ["powershell", "-NoProfile", "-Command", "Get-Clipboard"], \
                      ["clip"]
    if args.get("set"):
        subprocess.run(write, input=wanted, text=True, check=True,
                       timeout=ACTION_TIMEOUT)
        return {"set": True}
    got = subprocess.run(read, capture_output=True, text=True,
                         timeout=ACTION_TIMEOUT)
    return {"text": got.stdout[:MAX_OUTPUT]}


def do_notify(args: dict) -> dict:
    text = str(args.get("text", ""))[:300]
    title = str(args.get("title", "JARVIS"))[:100]
    if sys.platform == "darwin":
        command = ["osascript", "-e",
                   f'display notification {json.dumps(text)} '
                   f'with title {json.dumps(title)}']
    elif sys.platform.startswith("linux"):
        if not shutil.which("notify-send"):
            raise RuntimeError("No notify-send. Install libnotify-bin.")
        command = ["notify-send", title, text]
    else:
        command = ["powershell", "-NoProfile", "-Command",
                   f"[void][System.Reflection.Assembly]::LoadWithPartialName("
                   f"'System.Windows.Forms');"
                   f"[System.Windows.Forms.MessageBox]::Show("
                   f"{json.dumps(text)},{json.dumps(title)})"]
    subprocess.run(command, check=True, timeout=ACTION_TIMEOUT)
    return {"shown": True}


def do_speak(args: dict) -> dict:
    """Say it aloud on this machine. A Mac has `say` built in."""
    text = str(args.get("text", ""))[:1000]
    if not text.strip():
        raise RuntimeError("Nothing to say.")
    if sys.platform == "darwin":
        command = ["say", text]
    elif sys.platform.startswith("linux"):
        tool = next((t for t in ("spd-say", "espeak", "espeak-ng")
                     if shutil.which(t)), None)
        if tool is None:
            raise RuntimeError(
                "Nothing here can speak. Install one: apt install espeak")
        command = [tool, text]
    else:
        command = ["powershell", "-NoProfile", "-Command",
                   "Add-Type -AssemblyName System.Speech;"
                   "(New-Object System.Speech.Synthesis.SpeechSynthesizer)"
                   f".Speak({json.dumps(text)})"]
    subprocess.run(command, check=True, timeout=ACTION_TIMEOUT)
    return {"said": text}


def do_browser(args: dict) -> dict:
    """Open an address in this machine's own browser.

    Only http and https. A `file://` or a `javascript:` arriving from
    somewhere else is not a page to open, it is an instruction to this
    machine dressed as one.
    """
    url = str(args.get("url", ""))
    if not url.startswith(("http://", "https://")):
        raise RuntimeError(f"I will only open http and https addresses, not {url!r}.")
    if sys.platform == "darwin":
        command = ["open", url]
    elif sys.platform.startswith("linux"):
        command = ["xdg-open", url]
    else:
        command = ["cmd", "/c", "start", "", url]
    subprocess.run(command, check=True, timeout=ACTION_TIMEOUT)
    return {"opened": url}


HANDLERS = {
    "screenshot": do_screenshot,
    "files.read": do_files_read,
    "files.write": do_files_write,
    "terminal": do_terminal,
    "clipboard": do_clipboard,
    "notify": do_notify,
    "browser": do_browser,
    "speak": do_speak,
}


# --- the loop ---------------------------------------------------------------

def run_one(job: dict, granted: set) -> tuple[bool, dict, str]:
    capability = job.get("capability", "")
    # Checked HERE as well as on the server. The server decides what to
    # ask for; this decides what it is willing to do. A server that has
    # been tampered with does not thereby gain new hands.
    if capability not in granted:
        return False, {}, (
            f"This sidecar was not paired with '{capability}'. Refused here, "
            f"whatever the server asked for.")
    handler = HANDLERS.get(capability)
    if handler is None:
        return False, {}, f"This sidecar cannot do '{capability}'."
    try:
        return True, handler(job.get("arguments") or {}), ""
    except subprocess.TimeoutExpired:
        return False, {}, f"'{capability}' was still running after {ACTION_TIMEOUT}s."
    except Exception as exc:  # noqa: BLE001 - any failure is reported, not raised
        return False, {}, f"{type(exc).__name__}: {exc}"


def command_pair(base: str, code: str) -> None:
    answer = call(base, "/v1/sidecar/pair", {"code": code, "reported": describe()})
    CONFIG.write_text(json.dumps(
        {"base": base.rstrip("/"), "token": answer["token"],
         "name": answer["sidecar"]["name"],
         "capabilities": answer["sidecar"]["capabilities"]}, indent=2))
    CONFIG.chmod(0o600)
    say(f"Paired as {answer['sidecar']['name']}.")
    say("This machine may:", ", ".join(answer["sidecar"]["capabilities"]) or "nothing")
    say(f"Token saved to {CONFIG} (readable only by you).")
    say(f"Now run: {sys.argv[0]} run")


def command_repoint(base: str) -> None:
    """JARVIS moved. Same sidecar, new address.

    Moving JARVIS to another machine takes its database with it, so the
    pairing and the token survive the move -- only the address changes.
    Without this, every machine he owns would have to be re-paired by
    hand for a change that is one line in a file, and the temptation
    would be to skip the pairing step next time.
    """
    config = load()
    was = config["base"]
    config["base"] = base.rstrip("/")
    CONFIG.write_text(json.dumps(config, indent=2))
    CONFIG.chmod(0o600)
    say(f"Now reporting to {config['base']} (was {was}).")

    try:
        answer = call(config["base"], "/v1/sidecar/poll",
                      {"reported": describe(), "limit": 1}, config["token"])
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            say("That server does not know this sidecar. If JARVIS was moved "
                "WITHOUT its database, pair again from the dashboard there.")
        else:
            say(f"That server answered {exc.code}. The address is saved; check "
                f"it is the right one.")
        return
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        say(f"Cannot reach it yet ({exc}). The address is saved; try `run` "
            f"once the server is up.")
        return
    say("It answered and still knows this sidecar. May:",
        ", ".join(answer.get("you_may") or []) or "nothing")


def command_run() -> None:
    config = load()
    granted = set(config.get("capabilities") or [])
    say(f"{config['name']} reporting to {config['base']}.")
    say("May:", ", ".join(sorted(granted)) or "nothing")
    say("Ctrl-C to stop. Closing this stops JARVIS reaching this machine.")

    quiet = 0
    while True:
        try:
            answer = call(config["base"], "/v1/sidecar/poll",
                          {"reported": describe()}, config["token"])
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                say("The server no longer knows this sidecar. It was probably "
                    "revoked. Pair again if that was not deliberate.")
                return
            say(f"Server said {exc.code}. Waiting.")
            time.sleep(min(60, POLL_SECONDS * 2 ** min(quiet, 4))); quiet += 1
            continue
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            say(f"Cannot reach the server ({exc}). Waiting.")
            time.sleep(min(60, POLL_SECONDS * 2 ** min(quiet, 4))); quiet += 1
            continue

        quiet = 0
        jobs = answer.get("jobs") or []
        if not jobs:
            time.sleep(POLL_SECONDS)
            continue

        for job in jobs:
            ok, result, error = run_one(job, granted)
            say(("did " if ok else "refused ") + job.get("capability", "?")
                + (f" -- {error}" if error else ""))
            try:
                call(config["base"], f"/v1/sidecar/jobs/{job['id']}",
                     {"ok": ok, "result": result, "error": error},
                     config["token"])
            except Exception as exc:  # noqa: BLE001
                say(f"Could not report that back: {exc}")


def main() -> None:
    if len(sys.argv) >= 2 and sys.argv[1] == "pair" and len(sys.argv) == 4:
        return command_pair(sys.argv[2], sys.argv[3])
    if len(sys.argv) >= 2 and sys.argv[1] == "run":
        return command_run()
    if len(sys.argv) == 3 and sys.argv[1] == "repoint":
        return command_repoint(sys.argv[2])
    print(__doc__)
    sys.exit(2)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        say("Stopped. JARVIS can no longer reach this machine.")
