# Operations

Running Clockwork on a VPS so it outlives the laptop.

## VPS sizing

Two vCPU / 2 GB is enough for 2–3 workers; 4 vCPU / 4 GB carries 4–6. Beyond that the provider's connection cap
binds before the CPU does. Choose a region that is not the target's own (a different country is fine and is in fact
what the identity pool already does), and keep the disk small — everything is stateless except `data/`.

## Deploy

```bash
# on the VPS, as a non-root user in the docker group
git clone <your-repo> clockwork && cd clockwork
cp .env.example .env
$EDITOR .env                      # target, identities, proxy credentials
mkdir -p data
docker compose build
docker compose up -d --scale worker=4
docker compose logs -f --tail=50
```

## Survive reboots

```bash
sudo cp systemd/clockwork.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now clockwork
```

The unit restarts compose on boot and stops the stack cleanly. `restart: unless-stopped` in the compose file additionally
recovers individual containers.

## Health

```bash
docker compose ps                          # health column should stay "healthy"
tail -f data/hits.txt                      # the only line that matters
python - <<'EOF'
import json; d=json.load(open("data/ledger.json"))
print("next:", d["next"], "spent:", d["spent"], "done:", d.get("done"), "parked:", len(d.get("blocked", {})))
EOF
```

A worker whose heartbeat goes stale is restarted by the healthcheck. Empty logs plus a rising `parked` count means the
identity pool is exhausted and the run is cooldown-bound — add identities rather than workers.

## Stop and resume

```bash
docker compose stop          # resume with `up -d`; the ledger continues where it stopped
```

To start a fresh sweep, keep the ledger and set the new search space in it:

```bash
python - <<'EOF'
import json; p="data/ledger.json"; d=json.load(open(p))
d.update(next=500000, done=False); json.dump(d, open(p,"w"))
EOF
```

## Troubleshooting

| symptom | cause | fix |
|---|---|---|
| `load-fail` on every cycle | proxy cannot reach the target, or credentials wrong | `curl --socks5-hostname $PROXY_HOST:$PROXY_PORT -U $IDENTITIES:… https://api.ipify.org` |
| `flow token:…` | token endpoint unreachable through the exit | provider blocking that host; try another identity |
| `flow init:401/403` | wrong paths, client metadata or `LOGIN_ID` | re-check `.env` against captured traffic |
| every code returns `408` | identity already parked server-side | expected; wait out `COOLDOWN` or add identities |
| reset storms, no HTTP status | too many sockets per identity | lower `CONCURRENCY`, then lower `SLOTS` |
| duplicate work after restart | two stacks running | `docker compose ps` across both hosts; one ledger only |

## Bandwidth, and why images are off

Every cycle loads a real page, and pages are heavy. Images are disabled by default
(`--blink-settings=imagesEnabled=false`); set `BLOCK_IMAGES=0` only if the target's flow genuinely needs them.

Rough planning figure: a cycle is one page load, and a page load with images on can reach several megabytes. A 2 GB
monthly plan runs dry in a few thousand codes that way — a run of 10.5k codes exhausted one. With images off, expect
an order of magnitude less. Watch the provider's usage counter and top up before starting a long run; a proxy that
answers slowly and then hangs is the usual shape of an exhausted balance, not of a broken harness.

## Housekeeping

- `.env` holds the proxy password: keep it out of git, rotate on leak.
- `data/` grows slowly; `hits.txt` is append-only and safe to tail from any shell.
- Logs are the credential-free part of the record — keep them if the run matters.
