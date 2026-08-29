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
        PURPLE = LPURPLE = MUTED = BOLD = DIM = RESET = CLEAR = ""

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

def cmd_chat(cfg: dict):
    """Interactive streaming chat via WebSocket."""
    instance = get_instance(cfg)
    token = get_token(cfg)

    # Parse host/port/path from instance URL
    use_ssl = instance.startswith("https://")
    host_part = instance.replace("https://", "").replace("http://", "")
    if "/" in host_part:
        host_only, base_path = host_part.split("/", 1)
        base_path = "/" + base_path
    else:
        host_only = host_part
        base_path = ""

    if ":" in host_only:
        host, port_str = host_only.rsplit(":", 1)
        port = int(port_str)
    else:
        host = host_only
        port = 443 if use_ssl else 80

    ws_path = f"{base_path}/ws/chat"

    print(CLEAR)
    print(logo())
    print(f"  {MUTED}Connecting to {instance}...{RESET}")

    try:
        sock = ws_connect(host, port, ws_path, use_ssl=use_ssl, token=token)
    except Exception as e:
        print(f"\n  {MUTED}✗ Could not connect: {e}{RESET}")
        print(f"  {MUTED}Try: ado config instance <your-ada-url>{RESET}\n")
        return

    # Read the "ready" frame
    try:
        _, frame = ws_recv_frame(sock)
        data = json.loads(frame)
        ada_name = data.get("name", "Ada")
    except Exception:
        ada_name = "Ada"

    print(f"\033[2K\r  {PURPLE}● Connected to {ada_name}{RESET}")
    print(f"  {MUTED}Type a message. Ctrl+C or 'exit' to quit.{RESET}\n")

    def recv_loop():
        buf = ""
        in_stream = False
        try:
            while True:
                opcode, frame = ws_recv_frame(sock)
                if opcode == 8:  # close
                    break
                if opcode not in (1, 2):
                    continue
                try:
                    msg = json.loads(frame)
                except Exception:
                    continue

                t = msg.get("type")
                if t == "start":
                    in_stream = True
                    buf = ""
                    print(f"\n  {LPURPLE}{ada_name}:{RESET} ", end="", flush=True)
                elif t == "chunk":
                    chunk = msg.get("content", "")
                    print(chunk, end="", flush=True)
                    buf += chunk
                elif t == "done":
                    in_stream = False
                    print(f"\n", flush=True)
                elif t == "error":
                    print(f"\n  {MUTED}Error: {msg.get('content')}{RESET}\n", flush=True)
        except Exception:
            pass

    recv_thread = threading.Thread(target=recv_loop, daemon=True)
    recv_thread.start()

    try:
        while True:
            try:
                user_input = input(f"  {BOLD}You:{RESET} ").strip()
            except EOFError:
                break
            if not user_input:
                continue
            if user_input.lower() in ("exit", "quit", "bye"):
                break
            ws_send_text(sock, json.dumps({"content": user_input}))
    except KeyboardInterrupt:
        pass
    finally:
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

    cfg_p = sub.add_parser("config", help="Show or set configuration")
    cfg_p.add_argument("key",   nargs="?", help="Config key")
    cfg_p.add_argument("value", nargs="?", help="Config value")

    args = parser.parse_args()
    cfg = load_config()

    if args.cmd in (None, "chat"):
        cmd_chat(cfg)
    elif args.cmd == "status":
        cmd_status(cfg)
    elif args.cmd == "apps":
        cmd_apps(cfg)
    elif args.cmd == "login":
        cmd_login(cfg, args)
    elif args.cmd == "config":
        cmd_config(cfg, args)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
