"""One Clockwork worker: relay + browser per identity, ticks until the window closes."""
from __future__ import annotations

import json
import os
import socket
import sys
import time

import core

IDX = int(os.environ.get("WORKER_INDEX", "0"))
PORT = int(os.environ.get("PORT_BASE", "9101")) + IDX
DEBUG_PORT = int(os.environ.get("CHROME_PORT_BASE", "9301")) + IDX
BEAT = "/tmp/clockwork.heartbeat"


def beat() -> None:
    with open(BEAT, "w") as f:
        f.write(str(int(time.time())))


def identity() -> str:
    return core.IDENTITIES[IDX % len(core.IDENTITIES)]


def cycle(ledger: core.Ledger) -> int:
    user = identity()
    relay = core.Relay(PORT, user)
    relay.start()
    browser = core.Browser(PORT, DEBUG_PORT)
    page = None
    spent = 0
    try:
        page = browser.open(core.BASE + "/")
        loaded = False
        for _ in range(20):
            time.sleep(1.5)
            try:
                if core.TARGET_HOST in (page.host() or ""):
                    loaded = True
                    break
            except Exception:
                pass
        if not loaded:
            print("%s load-fail" % user, flush=True)
            return 0

        page.js(core.FLOW_JS)
        page.js("window.__CFG=%s; 'ok'" % json.dumps(core.flow_config()))

        ip = ""
        try:
            ip = page.js("fetch('https://api.ipify.org').then(r=>r.text())") or ""
        except Exception:
            pass
        if ip and ledger.blocked(ip):
            print("%s %s parked" % (user, ip), flush=True)
            return 0

        state = page.js("window.__start()") or ""
        if state != "ok":
            print("%s flow %s" % (user, state), flush=True)
            return 0

        for _ in range(core.PER_CYCLE):
            if ledger.finished():
                break
            code = ledger.next_code()
            if code is None:
                break
            status = str(page.js("window.__try('%s')" % code))
            spent += 1
            if status == "200":
                ledger.hit(code, ip)
                print("HIT %s (%s)" % (code, ip), flush=True)
                break
            if status == "408":
                if ip:
                    ledger.mark_blocked(ip)
                break
            if status[:1] == "4" and status != "400":
                # flow state stale (401) or shape rejected (403): reopen before retrying
                break
            time.sleep(0.3)
        print("%s %s spent %d" % (user, ip or "?", spent), flush=True)
        return spent
    finally:
        if page:
            page.close()
        browser.kill()
        relay.stop()


def main() -> int:
    ledger = core.Ledger(os.path.join(core.DATA, "ledger.json"))
    while not ledger.finished():
        try:
            n = cycle(ledger)
            if n:
                ledger.spend(n)
        except Exception as e:
            import traceback
            traceback.print_exc()
            print("cycle exc %s: %s" % (type(e).__name__, e), flush=True)
            time.sleep(6)
        beat()
    return 0


if __name__ == "__main__":
    sys.exit(main())
