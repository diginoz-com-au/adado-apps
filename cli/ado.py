#!/usr/bin/env python3
"""
ado — AdaDo CLI
Cross-platform command-line interface to your Ada instance.
Works on Linux, macOS, and Windows.

Usage:
  ado               interactive chat (TUI)
  ado chat          same as above
  ado status        show Ada's status and connected apps
  ado apps          list installed apps
  ado connect       connect a device to this Ada instance
  ado config        show / set configuration
"""
import os, sys, json, time, threading, argparse, signal
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError
from urllib.parse import urlencode

# ─── Config ───────────────────────────────────────────────────────────────────

CONFIG_DIR  = Path.home() / ".ado"
CONFIG_FILE = CONFIG_DIR / "config.json"
INSTANCE_DEFAULT = "https://adado.diginoz.com.au"

PURPLE  = "\033[38;5;99m"
LPURPLE = "\033[38;5;141m"
GREEN   = "\033[38;5;40m"
LGREEN  = "\033[38;5;83m"
MUTED   = "\033[38;5;245m"
BOLD    = "\033[1m"
DIM     = "\033[2m"
RESET   = "\033[0m"
CLEAR   = "\033[2J\033[H"

# Disable colour on Windows if no ANSI support
if sys.platform == "win32":
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
    except Exception:
        PURPLE = LPURPLE = GREEN = LGREEN = MUTED = BOLD = DIM = RESET = CLEAR = ""

def logo():
    return f"""{PURPLE}
  ╔═╗╔╦╗╔═╗╔╦╗╔═╗
  ╠═╣ ║║╠═╣ ║║║ ║
  ╩ ╩═╩╝╩ ╩═╩╝╚═╝{RESET}  {MUTED}Your AI. Your way.{RESET}
"""

# ─── Config helpers ───────────────────────────────────────────────────────────

def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except Exception:
            pass
    return {}

def save_config(cfg: dict):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))

def get_instance(cfg: dict) -> str:
    return cfg.get("instance", os.environ.get("ADO_INSTANCE", INSTANCE_DEFAULT)).rstrip("/")

def get_token(cfg: dict) -> str | None:
    return cfg.get("token") or os.environ.get("ADO_TOKEN")

def maybe_refresh_token(cfg: dict, instance: str) -> dict:
    """
    Silently refresh JWT if < 7 days left.
    Returns updated cfg (caller should save if changed).
    """
    token = get_token(cfg)
    if not token:
        return cfg
    try:
        import base64 as _b64, json as _json
        parts = token.split(".")
        if len(parts) != 3:
            return cfg
        payload = _json.loads(_b64.b64decode(parts[1] + "=="))
        exp = payload.get("exp", 0)
        days_left = (exp - __import__("time").time()) / 86400
        if days_left > 7:
            return cfg
        resp = api_post(f"{instance}/api/auth/refresh", {}, token=token)
        if resp.get("token"):
            cfg["token"] = resp["token"]
    except Exception:
        pass
    return cfg

# ─── API helpers ──────────────────────────────────────────────────────────────

def api_get(url: str, token: str | None = None) -> dict:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = Request(url, headers=headers)
    with urlopen(req, timeout=10) as r:
        return json.loads(r.read())

def api_post(url: str, data: dict, token: str | None = None) -> dict:
    body = json.dumps(data).encode()
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = Request(url, data=body, headers=headers)
    with urlopen(req, timeout=10) as r:
        return json.loads(r.read())

# ─── WebSocket chat (stdlib only, no ws library needed) ───────────────────────

import socket, base64, hashlib, struct, ssl

def ws_connect(host: str, port: int, path: str, use_ssl: bool = False, token: str | None = None):
    """Minimal WebSocket client — no external deps, works everywhere."""
    if use_ssl:
        ctx = ssl.create_default_context()
        sock = ctx.wrap_socket(socket.create_connection((host, port), timeout=30), server_hostname=host)
    else:
        sock = socket.create_connection((host, port), timeout=30)

    # Handshake
    key = base64.b64encode(os.urandom(16)).decode()
    full_path = path
    if token:
        full_path += f"?token={token}"

    handshake = (
        f"GET {full_path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        f"Upgrade: websocket\r\n"
        f"Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        f"Sec-WebSocket-Version: 13\r\n"
        f"\r\n"
    )
    sock.sendall(handshake.encode())

    # Read response headers
    buf = b""
    while b"\r\n\r\n" not in buf:
        buf += sock.recv(1024)

    expected = base64.b64encode(
        hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
    ).decode()
    if "101 Switching Protocols" not in buf.decode("utf-8", errors="ignore"):
        raise ConnectionError("WebSocket upgrade failed")

    return sock

def ws_recv_frame(sock: socket.socket) -> tuple[int, bytes]:
    """Read one WebSocket frame."""
    def recv_exact(n):
        data = b""
        while len(data) < n:
            chunk = sock.recv(n - len(data))
            if not chunk:
                raise ConnectionError("Connection closed")
            data += chunk
        return data

    header = recv_exact(2)
    opcode = header[0] & 0x0F
    length = header[1] & 0x7F

    if length == 126:
        length = struct.unpack("!H", recv_exact(2))[0]
    elif length == 127:
        length = struct.unpack("!Q", recv_exact(8))[0]

    return opcode, recv_exact(length)

def ws_send_text(sock: socket.socket, text: str):
    """Send a masked text frame."""
    payload = text.encode()
    mask = os.urandom(4)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    length = len(payload)

    if length < 126:
        header = bytes([0x81, 0x80 | length])
    elif length < 65536:
        header = bytes([0x81, 0xFE]) + struct.pack("!H", length)
    else:
        header = bytes([0x81, 0xFF]) + struct.pack("!Q", length)

    sock.sendall(header + mask + masked)

# ─── Commands ─────────────────────────────────────────────────────────────────

def cmd_status(cfg: dict):
    instance = get_instance(cfg)
    token = get_token(cfg)
    print(logo())
    print(f"  {BOLD}Instance:{RESET} {instance}")
    try:
        data = api_get(f"{instance}/api/status", token)
        print(f"  {BOLD}Backend: {RESET}{data.get('backend', '?')}")
        print(f"  {BOLD}Model:   {RESET}{data.get('model', '?')}")
        print(f"  {BOLD}Name:    {RESET}{data.get('instance', '?')}")
        print(f"\n  {PURPLE}● Connected{RESET}\n")
    except Exception as e:
        print(f"\n  {MUTED}✗ Could not reach instance: {e}{RESET}\n")

def cmd_apps(cfg: dict):
    instance = get_instance(cfg)
    token = get_token(cfg)
    try:
        apps = api_get(f"{instance}/api/apps", token)
        print(f"\n{BOLD}  Installed Apps{RESET}  {MUTED}({len(apps)} total){RESET}\n")
        for app in apps:
            icon = app.get("icon", "•")
            name = app.get("name", "?")
            desc = app.get("description", "")[:55]
            print(f"  {icon}  {BOLD}{name}{RESET}")
            print(f"      {MUTED}{desc}{RESET}")
        print()
    except Exception as e:
        print(f"  {MUTED}Error: {e}{RESET}")

def cmd_config(cfg: dict, args):
    if args.key and args.value:
        cfg[args.key] = args.value
        save_config(cfg)
        print(f"  {PURPLE}✓{RESET} Set {args.key} = {args.value}")
    elif args.key:
        print(f"  {args.key} = {cfg.get(args.key, MUTED + '(not set)' + RESET)}")
    else:
        print(f"\n{BOLD}  Configuration{RESET}  {MUTED}{CONFIG_FILE}{RESET}\n")
        for k, v in cfg.items():
            if k == "token":
                print(f"  {k} = {MUTED}{str(v)[:20]}...{RESET}")
            else:
                print(f"  {k} = {v}")
        if not cfg:
            print(f"  {MUTED}(no config — using defaults){RESET}")
        print()

def cmd_login(cfg: dict, args):
    instance = get_instance(cfg)
    import getpass
    print(f"\n  {BOLD}Sign in to{RESET} {instance}\n")
    email = input("  Email: ").strip()
    password = getpass.getpass("  Password: ")
    try:
        resp = api_post(f"{instance}/api/auth/login", {"email": email, "password": password})
        cfg["token"] = resp["token"]
        save_config(cfg)
        print(f"\n  {PURPLE}✓{RESET} Signed in as {resp.get('name', email)}\n")
    except Exception as e:
        print(f"\n  {MUTED}✗ Login failed: {e}{RESET}\n")

def _parse_instance(instance: str):
    use_ssl   = instance.startswith("https://")
    host_part = instance.replace("https://", "").replace("http://", "")
    if "/" in host_part:
        host_only, tail = host_part.split("/", 1)
        base_path = "/" + tail
    else:
        host_only, base_path = host_part, ""
    if ":" in host_only:
        host, port = host_only.rsplit(":", 1)
        port = int(port)
    else:
        host, port = host_only, (443 if use_ssl else 80)
    return host, port, base_path, use_ssl


def cmd_chat(cfg: dict):
    """Full-screen TUI chat via WebSocket."""
    instance = get_instance(cfg)
    token    = get_token(cfg)
    host, port, base_path, use_ssl = _parse_instance(instance)
    ws_path  = base_path + "/ws/chat"

    print(CLEAR + logo() + f"  {MUTED}Connecting...{RESET}")
    try:
        # Connect without token in URL to keep it out of nginx access logs
        sock = ws_connect(host, port, ws_path, use_ssl=use_ssl, token=None)
    except Exception as e:
        print(f"\n  {MUTED}✗ {e}{RESET}\n  {MUTED}Try: ado login{RESET}\n")
        return

    # Send auth frame as first message before anything else
    if token:
        try:
            ws_send_text(sock, json.dumps({"type": "auth", "token": token}))
        except Exception as e:
            print(f"\n  {MUTED}✗ Auth failed: {e}{RESET}\n")
            return

    ada_name = "Ada"; model_name = ""; user_name = ""
    try:
        _, frame = ws_recv_frame(sock)
        d = json.loads(frame)
        # If server returned an error (e.g. bad token), bail out gracefully
        if d.get("type") == "error":
            print(f"\n  {MUTED}✗ {d.get('message', 'Connection rejected')}{RESET}\n  {MUTED}Try: ado login{RESET}\n")
            try: sock.close()
            except Exception: pass
            return
        ada_name   = d.get("name",  "Ada")
        model_name = d.get("model", "").split("/")[-1]
        user_name  = d.get("user",  "")
    except Exception:
        pass

    # ── shared state ──────────────────────────────────────────────────────────
    msgs   = []                         # list of (role, text): "ada"|"you"|"sys"
    stream = {"active": False, "buf": ""}
    lock   = threading.Lock()
    dirty  = threading.Event(); dirty.set()
    alive  = threading.Event(); alive.set()

    def recv_loop():
        try:
            while alive.is_set():
                opcode, frame = ws_recv_frame(sock)
                if opcode == 8:
                    break
                if opcode not in (1, 2):
                    continue
                try:
                    msg = json.loads(frame)
                except Exception:
                    continue
                t = msg.get("type")
                with lock:
                    if t == "start":
                        stream["active"] = True
                        stream["buf"]    = ""
                    elif t == "chunk":
                        stream["buf"] += msg.get("content", "")
                    elif t == "done":
                        if stream["buf"]:
                            msgs.append(("ada", stream["buf"]))
                        stream.update({"active": False, "buf": ""})
                    elif t == "error":
                        msgs.append(("sys", "Error: " + msg.get("content", "?")))
                        stream["active"] = False
                dirty.set()
        except Exception:
            with lock:
                msgs.append(("sys", "Connection closed."))
            dirty.set()

    threading.Thread(target=recv_loop, daemon=True).start()

    # ── TUI ───────────────────────────────────────────────────────────────────
    def render_lines(cols):
        """Flatten messages into word-wrapped display lines: [(kind, text)]."""
        out    = []
        max_w  = max(cols - 4, 20)

        def wrap(text, indent):
            lines, cur, avail = [], "", max_w - indent
            for word in text.replace("\n", " \n ").split(" "):
                if word == "\n":
                    lines.append(cur); cur = ""; avail = max_w - indent
                elif cur and len(cur) + 1 + len(word) > avail:
                    lines.append(cur); cur = word; avail = max_w - indent
                else:
                    cur = (cur + " " + word).strip() if cur else word
            if cur:
                lines.append(cur)
            return lines or [""]

        with lock:
            all_msgs = list(msgs)
            if stream["active"] and stream["buf"]:
                all_msgs.append(("ada", stream["buf"]))

        for role, text in all_msgs:
            if role == "sys":
                out.append(("sys", "  " + text))
            else:
                label    = (ada_name if role == "ada" else "You") + ":"
                indent   = len(label) + 1
                wrapped  = wrap(text, indent)
                out.append((role + "_first", "  " + label + " " + wrapped[0]))
                for cont in wrapped[1:]:
                    out.append((role + "_cont", "  " + " " * indent + cont))
            out.append(("blank", ""))

        return out

    def run_tui(stdscr):
        import curses as C
        C.curs_set(1)
        C.use_default_colors()
        if C.has_colors():
            C.init_pair(1, C.COLOR_GREEN,  -1)   # ada / green accent
            C.init_pair(2, C.COLOR_WHITE,  -1)   # you
            C.init_pair(3, 8,              -1)   # muted (bright black)
            C.init_pair(4, C.COLOR_BLACK, C.COLOR_GREEN)  # status bar
            C.init_pair(5, C.COLOR_MAGENTA, -1)  # purple accent

        cGreen  = C.color_pair(1) | C.A_BOLD
        cYou    = C.color_pair(2) | C.A_BOLD
        cMuted  = C.color_pair(3)
        cStatus = C.color_pair(4)
        cNorm   = C.A_NORMAL

        KIND_ATTR = {
            "ada_first": cGreen, "ada_cont": cNorm,
            "you_first": cYou,   "you_cont": cNorm,
            "sys":       cMuted, "blank":    cNorm,
        }

        inp_buf = ""; cursor = 0; scroll = 0
        stdscr.timeout(80)
        stdscr.keypad(True)

        while alive.is_set():
            rows, cols = stdscr.getmaxyx()
            msg_h = max(rows - 3, 1)

            lines = render_lines(cols)
            total = len(lines)
            start = max(0, total - msg_h - scroll)

            # message pane
            for y, (kind, text) in enumerate(lines[start: start + msg_h]):
                try:
                    stdscr.move(y + 1, 0); stdscr.clrtoeol()
                    stdscr.addstr(y + 1, 0, text[:cols - 1], KIND_ATTR.get(kind, cNorm))
                except C.error:
                    pass
            for y in range(min(len(lines) - start, msg_h), msg_h):
                try:
                    stdscr.move(y + 1, 0); stdscr.clrtoeol()
                except C.error:
                    pass

            # status bar
            host_disp = instance.replace("https://", "").replace("http://", "")
            status = f" ● AdaDo  {host_disp}"
            if user_name:  status += f"  {user_name}"
            if model_name: status += f"  {model_name}"
            if scroll > 0: status += "  ↑ scrolled (↓ to return)"
            try:
                stdscr.addstr(0, 0, status[:cols - 1].ljust(cols - 1), cStatus)
            except C.error:
                pass

            # separator
            try:
                stdscr.addstr(rows - 2, 0, "─" * (cols - 1), cMuted)
            except C.error:
                pass

            # input bar
            prompt   = " › "
            inp_area = cols - len(prompt) - 1
            pan      = max(0, cursor - inp_area + 1)
            disp     = inp_buf[pan: pan + inp_area]
            try:
                stdscr.move(rows - 1, 0); stdscr.clrtoeol()
                stdscr.addstr(rows - 1, 0, prompt, cGreen)
                stdscr.addstr(rows - 1, len(prompt), disp)
                stdscr.move(rows - 1, len(prompt) + cursor - pan)
            except C.error:
                pass

            stdscr.refresh()
            dirty.clear()

            ch = stdscr.getch()
            if ch == -1:
                if stream["active"]: dirty.set()
                continue

            if ch == C.KEY_RESIZE:
                stdscr.clear()

            elif ch in (C.KEY_ENTER, 10, 13):
                msg = inp_buf.strip(); inp_buf = ""; cursor = 0
                if msg.lower() in ("exit", "quit", "/exit", "/quit"):
                    alive.clear(); break
                if msg:
                    with lock: msgs.append(("you", msg))
                    scroll = 0; dirty.set()
                    try:
                        ws_send_text(sock, json.dumps({"content": msg}))
                    except Exception:
                        with lock: msgs.append(("sys", "✗ Send failed."))
                        dirty.set()

            elif ch in (C.KEY_BACKSPACE, 127, 8):
                if cursor > 0:
                    inp_buf = inp_buf[:cursor - 1] + inp_buf[cursor:]
                    cursor -= 1

            elif ch == C.KEY_DC:
                inp_buf = inp_buf[:cursor] + inp_buf[cursor + 1:]

            elif ch == C.KEY_LEFT:
                if cursor > 0: cursor -= 1

            elif ch == C.KEY_RIGHT:
                if cursor < len(inp_buf): cursor += 1

            elif ch in (C.KEY_HOME, 1):   # Home / Ctrl+A
                cursor = 0

            elif ch in (C.KEY_END, 5):    # End / Ctrl+E
                cursor = len(inp_buf)

            elif ch == C.KEY_UP:
                scroll = min(scroll + 3, max(0, total - msg_h))

            elif ch == C.KEY_DOWN:
                scroll = max(0, scroll - 3)

            elif ch == C.KEY_PPAGE:
                scroll = min(scroll + msg_h, max(0, total - msg_h))

            elif ch == C.KEY_NPAGE:
                scroll = max(0, scroll - msg_h)

            elif 32 <= ch <= 126:
                inp_buf = inp_buf[:cursor] + chr(ch) + inp_buf[cursor:]
                cursor += 1

            dirty.set()

    # ── dumb-terminal fallback (Windows / no curses) ──────────────────────────
    def run_dumb():
        print(f"\033[2K\r  {GREEN}● {ada_name}{RESET}  {MUTED}{instance}{RESET}\n")
        print(f"  {MUTED}Type a message. 'exit' to quit.{RESET}\n")

        def _recv():
            try:
                while alive.is_set():
                    opcode, frame = ws_recv_frame(sock)
                    if opcode == 8: break
                    if opcode not in (1, 2): continue
                    try: msg = json.loads(frame)
                    except: continue
                    t = msg.get("type")
                    if t == "start":
                        print(f"\n  {LGREEN}{ada_name}:{RESET} ", end="", flush=True)
                    elif t == "chunk":
                        print(msg.get("content", ""), end="", flush=True)
                    elif t == "done":
                        print("\n", flush=True)
                    elif t == "error":
                        print(f"\n  {MUTED}Error: {msg.get('content')}{RESET}\n", flush=True)
            except Exception:
                pass

        threading.Thread(target=_recv, daemon=True).start()
        try:
            while alive.is_set():
                try:
                    text = input(f"  {BOLD}You:{RESET} ").strip()
                except EOFError:
                    break
                if not text: continue
                if text.lower() in ("exit", "quit"): break
                ws_send_text(sock, json.dumps({"content": text}))
        except KeyboardInterrupt:
            pass

    try:
        import curses as _c
        _c.wrapper(run_tui)
    except Exception:
        run_dumb()

    alive.clear()
    try:
        sock.close()
    except Exception:
        pass
    print(f"\n  {MUTED}Goodbye.{RESET}\n")

# ─── Entry point ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        prog="ado",
        description="AdaDo CLI — your AI, your way",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
commands:
  (none)       start interactive chat
  chat         start interactive chat
  status       check connection to Ada
  apps         list installed apps
  login        sign in to your Ada account
  config       show or set configuration

examples:
  ado                              # start chatting
  ado status                       # check Ada is reachable
  ado config instance https://...  # point to your Ada instance
  ado login                        # sign in for saved history
        """,
    )
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("chat",   help="Start interactive chat (default)")
    sub.add_parser("status", help="Show Ada status and connection info")
    sub.add_parser("apps",   help="List installed apps")
    sub.add_parser("login",  help="Sign in to your Ada account")
    sub.add_parser("logout", help="Sign out and clear saved token")

    cfg_p = sub.add_parser("config", help="Show or set configuration")
    cfg_p.add_argument("key",   nargs="?", help="Config key")
    cfg_p.add_argument("value", nargs="?", help="Config value")

    args = parser.parse_args()
    cfg = load_config()

    # Silently refresh JWT if expiring soon (< 7 days)
    if get_token(cfg):
        instance = get_instance(cfg)
        updated = maybe_refresh_token(cfg, instance)
        if updated.get("token") != cfg.get("token"):
            save_config(updated)
            cfg = updated

    if args.cmd in (None, "chat"):
        cmd_chat(cfg)
    elif args.cmd == "status":
        cmd_status(cfg)
    elif args.cmd == "apps":
        cmd_apps(cfg)
    elif args.cmd == "login":
        cmd_login(cfg, args)
    elif args.cmd == "logout":
        cfg.pop("token", None)
        cfg.pop("email", None)
        save_config(cfg)
        print("  Signed out. Run 'ado login' to sign back in.")
    elif args.cmd == "config":
        cmd_config(cfg, args)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
