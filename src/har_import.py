"""Derive a Clockwork .env from a browser HAR capture.

Point it at a HAR containing one pass of the target's reset flow. It classifies the
flow requests, lifts the metadata the harness needs, and writes a .env with blanks where
a human must decide (proxy credentials, login id if absent).

Usage:
    python -m har_import capture.har --out .env [--host account.example.com]
"""
from __future__ import annotations

import argparse
import base64
import json
import statistics
import sys
from urllib.parse import urlparse

SIGNED_HINT = "accept-language;language-pref;body"


def _payload(jwt: str) -> dict:
    try:
        part = jwt.split(".")[1]
        part += "=" * (-len(part) % 4)
        return json.loads(base64.urlsafe_b64decode(part))
    except Exception:
        return {}


def _body(entry: dict):
    text = (entry.get("request") or {}).get("postData", {}).get("text")
    if not text:
        return None, {}
    try:
        return text, json.loads(text)
    except Exception:
        return text, {}


def load(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        har = json.load(f)
    entries = har.get("log", {}).get("entries", [])
    if not entries:
        die("HAR has no entries")
    return entries


def die(msg: str) -> None:
    print("error: " + msg, file=sys.stderr)
    raise SystemExit(2)


def pick_host(entries: list[dict], wanted: str | None) -> str:
    hosts: dict[str, int] = {}
    for e in entries:
        h = urlparse(e["request"]["url"]).hostname or ""
        if h:
            hosts[h] = hosts.get(h, 0) + 1
    if wanted:
        if wanted not in hosts:
            die("host %s not present in the HAR" % wanted)
        return wanted
    if not hosts:
        die("no hostnames in the HAR")
    # the flow host is the one carrying JSON POSTs, not the busiest one overall
    posts = {}
    for e in entries:
        h = urlparse(e["request"]["url"]).hostname or ""
        if e["request"].get("method") == "POST" and (e["request"].get("postData") or {}).get("text"):
            posts[h] = posts.get(h, 0) + 1
    return max(posts, key=posts.get) if posts else max(hosts, key=hosts.get)


def classify(entries: list[dict], host: str) -> dict[str, dict]:
    found: dict[str, dict] = {}
    for e in entries:
        req = e["request"]
        u = urlparse(req["url"])
        if u.hostname != host or req.get("method") != "POST":
            continue
        text, body = _body(e)
        if not text:
            continue
        path = u.path
        if isinstance(body, dict):
            if ("otp" in body) and "enValue" in body:
                found.setdefault("verify", {"path": path, "body": body, "entry": e})
            elif "enValue" in body and ("option" in body or "value" in body):
                found.setdefault("send", {"path": path, "body": body, "entry": e})
            elif "clientPubKey" in body or ("loginId" in body and "flowIndicator" in body):
                found.setdefault("init", {"path": path, "body": body, "entry": e})
        resp = (e.get("response") or {}).get("content", {}).get("text") or ""
        if "id_token" in resp:
            found.setdefault("token", {"path": path, "host": host, "entry": e})
    return found


def token_host(entries: list[dict], host: str) -> str | None:
    for e in entries:
        resp = (e.get("response") or {}).get("content", {}).get("text") or ""
        req = e["request"]
        u = urlparse(req["url"])
        if "id_token" in resp and u.hostname and u.hostname != host:
            return u.hostname
    return None


def signed_headers(entries: list[dict]) -> str | None:
    for e in entries:
        for k, v in (e["request"].get("headers") or []):
            if k.lower() == "x-authorization":
                ehts = _payload(v).get("ehts")
                if ehts:
                    return ehts
    return None


def header_value(entries: list[dict], name: str) -> str | None:
    for e in entries:
        for k, v in (e["request"].get("headers") or []):
            if k.lower() == name.lower():
                return v
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="derive a Clockwork .env from a HAR capture")
    ap.add_argument("har")
    ap.add_argument("--out", default=".env.generated")
    ap.add_argument("--host", default=None, help="flow host, if detection picks wrong")
    ap.add_argument("--login-id", default=None)
    args = ap.parse_args(argv)

    entries = load(args.har)
    host = pick_host(entries, args.host)
    roles = classify(entries, host)

    missing = [r for r in ("init", "send", "verify") if r not in roles]
    if missing:
        die("HAR is missing flow step(s): %s — capture init → sendOtp → verifyOtp" % ", ".join(missing))

    init_body = roles["init"]["body"]
    token_h = token_host(entries, host) or host
    ehts = signed_headers(entries) or SIGNED_HINT

    login_id = args.login_id or init_body.get("loginId") or init_body.get("loginHint") or ""
    flow_indicator = init_body.get("flowIndicator", "password_reset")

    warnings: list[str] = []
    if not login_id:
        warnings.append("LOGIN_ID could not be lifted — set it by hand")
    if token_h == host:
        warnings.append("no separate token endpoint found — the flow may not use cnf exchange")
    if ehts != SIGNED_HINT:
        warnings.append("signed header list differs from the reference (%s); the in-page signer assumes %s"
                        % (ehts, SIGNED_HINT))

    token_path = roles["token"]["path"] if "token" in roles else ""
    lines = [
        "# generated from %s by har_import" % args.har,
        "# review every line; blanks are deliberate",
        "",
        "TARGET_HOST=" + host,
        "ADN_HOST=" + token_h,
        "TOKEN_PATH=" + token_path,
        "INIT_PATH=" + roles["init"]["path"],
        "SEND_PATH=" + roles["send"]["path"],
        "VERIFY_PATH=" + roles["verify"]["path"],
        "",
        "LOGIN_ID=" + str(login_id),
        "CLIENT_ID=" + str(init_body.get("clientId", "")),
        "FLOW_INDICATOR=" + str(flow_indicator),
        "SCOPE=" + str(init_body.get("scope", "openid")),
        "REDIRECT_URI=" + str(init_body.get("redirectUri", "")),
        "",
        "PROXY_HOST=",
        "PROXY_PORT=1080",
        "PROXY_PASS=",
        "IDENTITIES=",
        "",
        "PER_CYCLE=20",
        "COOLDOWN=660",
        "CONCURRENCY=3",
        "SLOTS=3",
        "",
        "SIGNED_HEADERS=" + ehts,
        "ACCEPT_LANGUAGE=" + (header_value(entries, "accept-language") or "en"),
        "LANGUAGE_PREF=" + (header_value(entries, "language-pref") or "en"),
    ]

    with open(args.out, "w") as f:
        f.write("\n".join(lines) + "\n")

    print("wrote %s" % args.out)
    print("host: %s  init/send/verify detected" % host)
    for w in warnings:
        print("warn: " + w)
    if "token" not in roles:
        print("warn: no cnf token exchange found; the signer will fail until TOKEN_PATH is set")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
