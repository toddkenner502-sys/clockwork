import base64
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import har_import  # noqa: E402


def jwt_with_ehts(ehts="accept-language;language-pref;body"):
    payload = base64.urlsafe_b64encode(json.dumps({"ehts": ehts}).encode()).decode().rstrip("=")
    return "h." + payload + ".s"


def entry(url, body, resp="{}", headers=None):
    return {
        "request": {
            "method": "POST",
            "url": url,
            "headers": headers or [{"name": "accept-language", "value": "en"},
                                   {"name": "language-pref", "value": "en"}],
            "postData": {"text": json.dumps(body)},
        },
        "response": {"content": {"text": resp}},
    }


def build_har():
    return {"log": {"entries": [
        entry("https://adn.example.com/guest/v1/tokens", {"cnf": "PEM", "client_id": "SIDWeb"},
              resp='{"id_token":"x.y.z"}'),
        entry("https://account.example.com/eui/v1/init", {
            "clientPubKey": "PEM", "loginId": "4042007239", "loginHint": "4042007239",
            "flowIndicator": "password_reset", "clientId": "MYTMO",
            "scope": "openid profile", "redirectUri": "https://www.example.com/signin"}),
        entry("https://account.example.com/eui/v1/send", {
            "option": "sms", "value": "xxx-xxx-7239", "enValue": "EN"}),
        entry("https://account.example.com/eui/v1/verify", {
            "enValue": "EN", "otp": "000000", "id": "xxx-xxx-7239"}),
        entry("https://cdn.example.com/app.js", {}, headers=[{"name": "X-Authorization", "value": jwt_with_ehts()}]),
    ]}}


def test_generates_env(tmp_path, capsys):
    har = tmp_path / "capture.har"
    har.write_text(json.dumps(build_har()))
    out = tmp_path / ".env.generated"

    rc = har_import.main([str(har), "--out", str(out)])
    assert rc == 0

    text = out.read_text()
    assert "TARGET_HOST=account.example.com" in text
    assert "ADN_HOST=adn.example.com" in text
    assert "INIT_PATH=/eui/v1/init" in text
    assert "SEND_PATH=/eui/v1/send" in text
    assert "VERIFY_PATH=/eui/v1/verify" in text
    assert "LOGIN_ID=4042007239" in text
    assert "SIGNED_HEADERS=accept-language;language-pref;body" in text
    assert "PROXY_PASS=\n" in text  # left blank for the human


def test_missing_flow_step_fails(tmp_path):
    har = tmp_path / "capture.har"
    har.write_text(json.dumps({"log": {"entries": [
        entry("https://account.example.com/eui/v1/init", {"clientPubKey": "PEM", "loginId": "1"})]}}))
    try:
        har_import.main([str(har), "--out", str(tmp_path / "x")])
    except SystemExit as e:
        assert e.code == 2
    else:
        raise AssertionError("expected failure when a flow step is absent")
