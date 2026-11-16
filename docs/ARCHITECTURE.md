# Architecture

```
        ┌──────────── worker container (one per identity) ────────────┐
        │  worker.py                                                  │
        │    ├── Relay :9101+n   local no-auth SOCKS5 → sticky exit    │
        │    ├── Chromium        headless, proxied at the relay        │
        │    │     └── page JS   signs each request, drives the flow   │
        │    └── ledger claims   monotonic codes, blocked-identity map │
        └──────────────┬──────────────────────────────┬───────────────┘
                       │ shared volume                │ SOCKS5 + auth
                  data/ledger.json               sticky residential exit
                  data/hits.txt                  (one identity per worker)
```

## Why one identity per container

The budget is per egress IP, so the unit of parallelism is the identity, not the process. A worker owns exactly one
identity: it binds a relay to it, points its browser at that relay, and when the identity is rejected it exits the cycle
so the next one can start — the identity itself is parked by timestamp, not by the process.

Hostname ordinals (`worker-1`, `worker-2`, …) map to identities by position, so `--scale worker=N` distributes
identities without a scheduler. When N exceeds the identity list the list is reused; collisions cost one guess each and
are cheaper than idle slots.

## Component notes

**Relay** — a local SOCKS5 listener with no authentication in front of an authenticated upstream. It exists for two
reasons: residential providers commonly cannot resolve target hostnames when the browser asks them to (Chrome resolves
proxy hostnames at the proxy), so resolution is pulled local and the upstream dialled by IPv4; and it caps upstream
sockets with a semaphore, because an unconstrained browser against a metered endpoint presents as reset flapping rather
than saturation.

**Page JS** — the whole binding scheme lives here: key generation, the `cnf` token exchange, and the detached signature
whose digest covers `accept-language`, `language-pref` and the body. It runs in the target's own origin so cookies,
`credentials: include` and the edge fingerprint are genuine. Nothing outside the browser can forge an acceptable request.

**Ledger** — one JSON file under `flock`: `next` (monotonic code cursor), `spent`, `blocked` (identity → timestamp),
`done`. Monotonic rather than random so a restart resumes and no candidate is bought twice; `done` stops every worker at
once on a hit.

## Deliberate trade-offs

- **Sticky identities, not rotation.** A rotating endpoint hands consecutive connections the same exit, and a browser
  multiplexes over one connection, so rotation collapses a whole batch onto one budget.
- **Serial per identity.** Concurrency is bounded by identity count and the provider's connection cap, not by CPU.
  Chromium's cost is a launch per cycle, roughly ten seconds, which is acceptable against a ten-minute cooldown.
- **No success handler.** `CHANGE_PATH` is configured but unused; the verified state is target-specific once a code
  lands, and the hit is recorded rather than acted on.
- **No database.** One file and `flock` survives reboots and needs no daemon; at a few hundred writes a minute it is
  nowhere near a bottleneck.
