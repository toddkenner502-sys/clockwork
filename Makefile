.PHONY: build up down logs scale status ledger clean

build:
	docker compose build

up:
	docker compose up -d

down:
	docker compose down

logs:
	docker compose logs -f --tail=50

scale:
	docker compose up -d --scale worker=$(N)

status:
	docker compose ps

ledger:
	python -c "import json;d=json.load(open('data/ledger.json'));print('next',d['next'],'spent',d['spent'],'done',d.get('done'),'parked',len(d.get('blocked',{})))"

clean:
	rm -rf data/ledger.json data/hits.txt
