# Clockwork

A harness for spending rate-limited OTP attempt budgets deliberately.

Clockwork treats the per-IP attempt budget as the scarce resource: it measures the budget, pins one egress identity per
worker, spends that identity's window, parks it until its cooldown expires, and keeps a monotonic ledger so no code is
tested twice. The name is the model — the clock is the budget window, a tick is one guess, a hand is one identity.

It exists because the naive approach fails twice over: the target counts attempts per source IP rather than per session,
and it binds requests with a proof-of-possession signature so nothing can be scripted from outside the browser.

Full method write-up, claims and measured results: [`docs/CLOCKWORK.md`](docs/CLOCKWORK.md).

## What it does, concretely

For a target whose reset flow is `init → sendOtp → verifyOtp` behind a signed request layer:

1. Drives a real browser (headless Chromium) through CDP so the edge fingerprint is genuine.
2. Re-implements the page's own request binding in-page: an RSA key pair, a confirmation (`cnf`) token exchange, and a
   detached signature whose digest covers `accept-language`, `language-pref` and the request body.
3. Opens a flow, then submits candidate codes until the identity is rejected.
4. Records the blocked identity with a timestamp and swaps to a fresh one; the ledger keeps the search monotonic.

## Requirements

- Docker + Docker Compose (the image carries Chromium)
- A proxy provider offering **sticky** SOCKS5 identities (one stable IP per username)

Rotating-only endpoints work but roughly halve the rate, because a browser multiplexes and inherits one exit for a
whole batch.

## Quickstart

```bash
cp .env.example .env
$EDITOR .env           # target, identity list, proxy credentials
docker compose build
docker compose up --scale worker=4
```

Watch `data/hits.txt`. The ledger lives in `data/ledger.json`, so restarts resume instead of replaying.

## Configuration

All via environment (see `.env.example`):

| variable | meaning |
|---|---|
| `TARGET_HOST` | origin to drive, e.g. `account.example.com` |
| `ADN_HOST` | token endpoint host for the `cnf` exchange |
| `TOKEN_PATH` | path on `ADN_HOST` returning `{"id_token": …}` |
| `INIT_PATH` / `SEND_PATH` / `VERIFY_PATH` | flow endpoints |
| `LOGIN_ID` | subscriber identifier the flow is opened for |
| `CLIENT_ID`, `SCOPE`, `FLOW_INDICATOR` | flow metadata required by the target |
| `PROXY_HOST`, `PROXY_PORT`, `PROXY_PASS` | sticky SOCKS5 upstream |
| `IDENTITIES` | comma-separated sticky usernames, one identity each |
| `PER_CYCLE` | guesses per identity window (keep under the measured tolerance) |
| `COOLDOWN` | seconds before a blocked identity is re-admitted |
| `CONCURRENCY` | upstream sockets a worker may hold at once |

Defaults are the values measured against the reference target; they are a starting point, not a guarantee.

## Measured reference run

Target: a US carrier's password-reset OTP, six numeric digits enforced server-side.

| quantity | value |
|---|---|
| secret space | 1,000,000 |
| tolerance per IP | 22–27 guesses |
| cooldown | > 5 min, ≈ 11 min observed |
| block signature | silent `408` |
| identities | 10 |
| throughput | 1.3 codes/s peak, 0.32 codes/s over nine hours |
| nine-hour result | 10,524 codes, no hit (2.8% of space) |

## Limits

- Sequential per identity, parallel only by identity count.
- A small identity pool degrades to cooldown-dominated.
- Success handling (`changePassword`) is target-specific and not wired by default.
- It reduces cost per attempt; it does not shrink the search space.

## Layout

```
src/core.py     ledger, identity pool, SOCKS relay, browser control, flow driver
src/worker.py   one worker loop (relay + browser + ticks)
src/flow.js     in-page signer and flow primitives, configured at runtime
docs/           method disclosure, architecture, operations
```

## Status

Reference implementation, tested against one target family. Expect to adjust the flow paths and metadata for another.

## License

**Clockwork License v1.0** — see `LICENSE`. In short:

- **Free to use, including commercially.** Client work, bug bounties, internal research: no fee.
- **If you sell it**, or ship it inside something you sell, a royalty of **up to 20%** of the gross receipt is owed to
  the copyright holder. Lower rates by written agreement.
- Attribution, a sales statement on request, and a 30-day cure period before the grant terminates.
- Non-payment or non-attribution is litigable, including costs and enhanced damages for wilful breach.

The method write-up in `docs/CLOCKWORK.md` is published for priority and licensed on the same terms. Commercial
licensing: toddkenner502@gmail.com.

