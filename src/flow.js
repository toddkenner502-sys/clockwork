(() => {
  // In-page signer and flow primitives. Configured at runtime via window.__CFG.
  const B64 = u8 => { let s = ""; const c = 0x8000; for (let i = 0; i < u8.length; i += c) s += String.fromCharCode.apply(null, u8.subarray(i, i + c)); return s; };
  const B64U = u8 => btoa(B64(u8)).replace(/[+]/g, "-").replace(/[/]/g, "_").replace(/=+$/, "");
  const hex = n => [...crypto.getRandomValues(new Uint8Array(n))].map(x => x.toString(16).padStart(2, "0")).join("");

  window.__C = { kp: null, tok: null, enValue: null, value: null, transId: null, opt: null };

  window.__sign = async (body) => {
    const C = window.__CFG;
    const edts = B64U(new Uint8Array(await crypto.subtle.digest("SHA-256",
      new TextEncoder().encode(C.acceptLanguage + C.languagePref + body))));
    const now = Math.floor(Date.now() / 1000);
    const pl = { iat: now, exp: now + 120, ehts: "accept-language;language-pref;body", edts, jti: crypto.randomUUID(), v: 1 };
    const u = o => B64U(new TextEncoder().encode(JSON.stringify(o)));
    const si = u({ alg: "RS256", typ: "JWT" }) + "." + u(pl);
    const sig = await crypto.subtle.sign({ name: "RSASSA-PKCS1-v1_5" }, window.__C.kp.privateKey, new TextEncoder().encode(si));
    return {
      "accept-language": C.acceptLanguage,
      "language-pref": C.languagePref,
      "content-type": "application/json",
      "accept": "application/json, text/plain, */*",
      "traceparent": "00-" + hex(16) + "-" + hex(8) + "-01",
      "Authorization": "Bearer " + window.__C.tok,
      "X-Authorization": si + "." + B64U(new Uint8Array(sig))
    };
  };

  window.__post = async (path, body) => {
    const h = await window.__sign(body);
    const r = await fetch(window.__CFG.base + path, { method: "POST", credentials: "include", headers: h, body });
    return { status: r.status, text: (await r.text()).slice(0, 400) };
  };

  window.__start = async () => {
    const C = window.__CFG;
    const kp = await crypto.subtle.generateKey(
      { name: "RSASSA-PKCS1-v1_5", modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" },
      true, ["sign", "verify"]);
    window.__C.kp = kp;
    const line = btoa(B64(new Uint8Array(await crypto.subtle.exportKey("spki", kp.publicKey))));
    const flat = "-----BEGIN PUBLIC KEY-----" + line + "-----END PUBLIC KEY-----";
    const multiline = "-----BEGIN PUBLIC KEY-----\n" + (line.match(/.{1,64}/g) || []).join("\n") + "\n-----END PUBLIC KEY-----";
    window.__C.transId = Date.now() + "_pcpcc_" + String(Math.floor(Math.random() * 1e10)).padStart(10, "0");

    let tok = null;
    try {
      const tr = await fetch(C.adn + C.tokenPath, {
        method: "POST", credentials: "include", headers: { "content-type": "application/json" },
        body: JSON.stringify({ cnf: flat, client_id: C.clientId })
      });
      const tj = await tr.json();
      tok = tj.id_token || tj.access_token || null;
    } catch (e) { return "token:" + e.message; }
    if (!tok) return "token:empty";
    window.__C.tok = tok;

    const init = JSON.stringify({
      redirectUri: C.redirectUri, scope: C.scope, clientId: C.clientId, accessType: "ONLINE",
      responseType: "code", approvalPrompt: "auto", loginId: C.loginId, loginHint: C.loginId,
      flowIndicator: C.flowIndicator, clientPubKey: multiline, isAppRequest: false, transId: window.__C.transId
    });
    const r1 = await window.__post(C.initPath, init);
    if (r1.status !== 200) return "init:" + r1.status;

    let j1 = {};
    try { j1 = JSON.parse(r1.text); } catch (e) { return "init:parse"; }
    const o = (j1.options && j1.options[0]) || {};
    window.__C.enValue = o.enValue || null;
    window.__C.value = o.value || null;
    window.__C.opt = o.id || null;
    if (!window.__C.enValue) return "init:no-envelope";

    const send = JSON.stringify({
      option: window.__C.opt, value: window.__C.value, enValue: window.__C.enValue,
      flowIndicator: C.flowIndicator, transId: window.__C.transId, clientId: C.clientId
    });
    const r2 = await window.__post(C.sendPath, send);
    return r2.status === 200 ? "ok" : "send:" + r2.status;
  };

  window.__try = async (code) => {
    const C = window.__CFG;
    const body = JSON.stringify({
      enValue: window.__C.enValue, otp: String(code), id: window.__C.value,
      transId: window.__C.transId, clientId: C.clientId
    });
    const r = await window.__post(C.verifyPath, body);
    return r.status;
  };

  return "ready";
})()
