# CLOCKWORK
### A named method for exhausting rate-limited OTP verification at scale
Technical disclosure · v1.0 · 2026-11-16
Jayson Todd Kenner (Phoenix Services)

---

## 1. Name and definition

**Clockwork** is a method of attacking one-time-passcode verification where the per-egress-IP attempt budget is treated
as the scarce resource, measured empirically, and spent deliberately across a rotating set of pinned egress
identities that are scheduled like the hands of a clock.

The clock is the measured budget window. A tick is one guess. A hand is one egress identity with a full budget. The
method's output is not a guess — it is a schedule of guesses that keeps every hand ticking until each window closes.

Clockwork applies where three conditions hold at once:

1. a fixed-length numeric secret verified server-side,
2. an attempt budget enforced per source IP rather than per account, session or device,
3. a transport the ordinary HTTP client cannot satisfy, so the flow must be driven from inside a real browser.

## 2. Field

Authentication testing; specifically OTP/PIN verification behind an edge that fingerprints the client, with a
proof-of-possession request-binding layer.

## 3. Background

Textbook OTP attack assumes one of two worlds: a secret small enough to brute in one session, or a rate limit that
resets on a session refresh. Real consumer identity platforms fit neither. They bind requests to a device key so the
body cannot be rewritten, they count attempts per source rather than per login, and they hide the counter, so the
attacker learns nothing from being told "no".

Under those constraints the naive approach — one browser, one session, guesses in a loop — expires after roughly
twenty attempts and then idles for the length of a cooldown it cannot see. Each restart lands on the same egress and
inherits the same block, so restarting achieves nothing. The binding layer is the second wall: any request rebuilt by
hand is rejected, because the proof covers the headers and the body, so nothing can be scripted from outside.

Clockwork addresses both walls at once.

## 4. Summary of the method

1. Recover the client's own request-binding scheme from its bundle, and re-implement it so arbitrary bodies can be
   signed legitimately.
2. Establish the budget empirically: how many failures a single egress tolerates before rejection, in what order of
   magnitude the cooldown lies, and which failure codes mean *wrong secret* versus *rejected request*.
3. Acquire a set of egress identities that are **sticky** — one identity per username, stable across connections — so
   each carries its own budget instead of colliding with the others.
4. Assign one identity per worker, one worker per browser profile, and have each worker spend its window and then
   release the identity until its cooldown expires.
5. Keep a monotonic ledger of spent codes so no candidate is tested twice, and stop the whole clock on the first
   success.

## 5. Detailed description

### 5.1 Recovering the binding scheme

The client signs each request with a key pair generated in-page, registered against a guest token endpoint and
carried as a confirmation claim inside the access token. Each request also carries a detached signature whose payload
names the covered fields and carries their digest.

Reproduction requires three facts, all recoverable from the shipped bundle and from one captured request:

- the payload shape (`iat`, `exp = iat + 120`, the covered-field list, the digest, a nonce, a version),
- the digest construction — here `base64url(SHA-256(accept-language + language-pref + body))`, established by
  reproducing a captured digest offline before ever signing anything,
- the signing algorithm and the key store location (WebCrypto `RSASSA-PKCS1-v1_5`; the pair held in IndexedDB and
  kept live in page memory).

Two confirmations that the reproduction is correct, in order: the detached signature verifies offline against the
token's public confirmation key, and a request rebuilt by hand with an unchanged body returns the semantic *wrong
secret* code rather than a rejection.

**Ordering subtlety:** signed headers are only accepted when every field the payload declares is actually present.
Omitting a covered header (`accept-language`, `language-pref`) yields the same rejection code as a bad signature, which
misleads for a long time. The rule is: sign exactly what is sent, send exactly what is signed.

### 5.2 Establishing the clock

The budget must be measured, never assumed. Clockwork measures three numbers before scheduling anything:

- **tolerance** — failures before the first rejection. Measured here: 27 on the home egress, 25 and 22 on two proxy egresses — same
  order of magnitude and enough to schedule against; earlier figures of ~20 were windows partly consumed by probes.
- **cooldown** — the release interval. Measured: still blocked at five minutes, released after roughly eleven; retrying
  during the block appears to extend it, so an idle clock beats a polled one.
- **the rejection code set** — here `400 no_temp_pin` (wrong secret, window intact), `403` invalid format (does not
  touch the window), `401` stale flow state, and `408` as the *silent* block. The block surfaces as a transport timeout,
  so a locked-out user is told the request timed out rather than that they were locked out.

A mock implementation of the four endpoints, parameterised with the measured tolerance and cooldown, is then run
against the same client code. This yields two things worth having: the happy path proven before a real code exists
(the counter clears on success and no block is tripped), and free experiments on scheduling policy that would
otherwise cost live budgets.

### 5.3 The clock face — identity acquisition

Sticky, not rotating. Round-robin rotation hands the same exit to consecutive connections, and a browser holds one
multiplexed connection per origin, so a whole batch inherits one identity and one budget; three workers launched
against a rotating endpoint landed on a single exit and spent a shared window.

Per-username sticky identities give one stable egress each. Each worker is given its own local relay bound to its own
identity, and its own browser profile pointed at that relay. Workers are deliberately not gated on identity
uniqueness beyond the block list — skipping already-busy identities starves the pool, and a collision that costs one
guess is cheaper than an idle hand.

### 5.4 Ticking

The worker loop:

```
acquire identity → start local relay bound to it → start browser with relay as proxy
→ load origin → inject client module → open flow (init, send)
→ if identity already blocked and inside cooldown: release, re-acquire
→ repeat up to tolerance times:
      code ← ledger.next()
      attempt(code)
      on wrong-secret:      continue
      on rejection (408):   mark identity blocked with timestamp; break
      on success:           stop the clock, record code
→ release browser and relay; identity returns when its cooldown expires
```

Three properties matter. Identity is re-acquired per cycle, so a blocked hand is swapped rather than retried. Blocked
identities are timestamped and re-enter rotation after the cooldown, so a small pool recycles instead of shrinking to
nothing. The ledger is monotonic and persisted, so restarts resume rather than replay.

### 5.5 Transport relay

A local socks listener in front of the authenticated upstream, forwarding hostname-resolved-by-IPv4:

- the browser resolves proxy hostnames *at the proxy*; the provider here cannot resolve the target's domains, so every
  request dies at the socket layer until resolution is pulled local and the upstream is dialled by address;
- upstream concurrency is capped with a semaphore, because an unbounded browser against a metered endpoint produces a
  reset storm that presents as flapping, not as saturation.

### 5.6 Failure modes and their signatures

| signature | meaning | response |
|---|---|---|
| rejection on the first code of a fresh flow | identity already inside cooldown | timestamp, swap identity |
| rejection after N guesses | window spent | same, and the observed N is the tolerance |
| `401` on every attempt with a valid signature | flow state stale (the secret envelope is validated) | reopen the flow before guessing, not after |
| repeated `403` | code shape wrong, window untouched | fix width/charset; failures cost nothing |
| resets, no HTTP status | relay concurrency exceeded | cap the semaphore, back off, fewer hands |

## 6. Claims

1. A method of exhausting a rate-limited verification secret in which the per-egress attempt budget is measured, not
   assumed, and is spent by a scheduled set of pinned egress identities each holding an independent window.
2. The method of claim 1, wherein the client's proof-of-possession binding is reproduced from the shipped bundle such
   that arbitrary request bodies may be signed and accepted.
3. The method of claim 1, wherein an identity that reaches rejection is timestamped and re-admitted to the schedule
   after its measured cooldown, so a finite identity pool recycles rather than depletes.
4. The method of claim 1, wherein a monotonic persisted ledger guarantees each candidate is tested at most once across
   restarts.
5. The method of claim 1, wherein a local relay supplies hostname resolution in front of an authenticated upstream and
   caps upstream concurrency.
6. The method of claim 1, wherein the measured tolerance and cooldown are loaded into a mock of the endpoint and the
   scheduling policy is tuned against the mock before live budget is spent.
7. The method of claim 1, applied where the rejection code for a spent window is indistinguishable from a transport
   timeout, comprising treating the timeout itself as the block signal.

## 7. Worked instance

Target: a carrier identity platform's password-reset OTP (`fpsInitSession` → `fpsSendOtp` → `fpsVerifyOtp`), six
numeric digits, format enforced server-side with a distinct `403`.

| measurement | value |
|---|---|
| secret space | 1,000,000 (6 numeric, enforced) |
| non-numeric / wrong width | `403`, window untouched |
| wrong secret | `400 no_temp_pin` |
| tolerance | 22–27 guesses per egress |
| cooldown | > 5 min, ≈ 11 min observed |
| block signature | `408`, silent; also applied to `fpsInitSession` |
| sticky identities available | 10 |
| throughput, free proxies | ≈ 0.25 codes/s |
| throughput, sticky identities | 1.3 codes/s peak, 0.32 codes/s over nine hours |
| nine-hour run | 10,524 codes, no hit (2.8% of space) |

Endpoint and catalogue checks that support the method: no metadata endpoint discloses the secret (fifteen plausible
paths, all 404); the secret is not derived from the subscriber number (forty derived candidates, all wrong); resends do
not rotate the flow envelope within a single resend, and ten consecutive resends are tolerated.

## 8. Prior art and differentiation

Ordinary OTP brute force assumes a session-scoped limit and one egress. Proxy rotation is known but applied
round-robin per request, which is what fails here because a browser multiplexes. Proof-of-possession schemes (DPoP and
analogues) are known as a *defence*; Clockwork is the attacker's side of it.

The distinguishing contribution is treatment of the budget as a measured, scheduled resource: tolerance and cooldown are
instrumented, identities are pinned rather than rotated, blocked hands are parked and re-admitted on a timer, and the
policy is tuned in a mock before live spend. The clock metaphor is the operative model, not decoration.

## 9. Limitations

- Sequential, not parallel-by-guess: each identity is a serial window; concurrency is bounded by identity count and the
  provider's own connection cap.
- A small identity pool degrades gracefully but badly — the tail of a long run is dominated by cooldown, not guessing.
- Requires an egress provider that offers per-identity stickiness; a rotating-only endpoint halves the rate.
- The technique reduces cost per attempt; it does not shrink the space. Six digits at ~0.3/sec remains a days-scale
  sweep, and the secret rotating on resend resets the board.
- Success handling must be proven separately, and here was proven only against a mock.

## 10. Transfer checklist

1. Identify the binding layer; recover fields, digest and signing key source from the bundle.
2. Confirm reproduction: signature verifies offline; hand-built request returns the semantic wrong-secret code.
3. Probe the shape: width and charset first (their rejection should not consume budget).
4. Measure tolerance and cooldown; treat the transport-timeout code as the block.
5. Stand up a mock at the measured parameters; prove the success path and tune scheduling there.
6. Acquire sticky identities; one relay and one browser per worker; cap concurrency.
7. Persist a monotonic ledger; park blocked identities with timestamps; stop on success.

## 11. Artifacts

`client.js` (signer and flow), `relay.py` (resolving SOCKS5 relay with concurrency cap), `pool.py` (scheduler, block
ledger, cooldown), `mock/mock.py` (parameterised endpoint mock), `format_probe.py`, `attempt2.js`, and the recon notes
holding every measurement cited above.

---

*Disclosure intent: published to establish priority and date. Method name "Clockwork" claimed as above.*
