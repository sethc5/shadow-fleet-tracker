.PHONY: install test ingest score digest serve status track track-all export map site discover daemon daemon-start daemon-stop publish telegram-send telegram-bot sync-osintukraine clean docker-build docker-ingest docker-score docker-digest

VENV := .venv/bin/python

install:
	python3 -m venv .venv
	.venv/bin/pip install -e .
	.venv/bin/pip install pytest

test:
	$(VENV) -m pytest tests/ -v

ingest:
	$(VENV) -m src.cli ingest --source all

ingest-ofac:
	$(VENV) -m src.cli ingest --source ofac

ingest-opensanctions:
	$(VENV) -m src.cli ingest --source opensanctions

ingest-tankertrackers:
	$(VENV) -m src.cli ingest --source tankertrackers

score:
	$(VENV) -m src.cli score

digest:
	$(VENV) -m src.cli digest

serve:
	$(VENV) -m src.cli serve

track:
	@test -n "$(IMO)" || (echo "Usage: make track IMO=1234567" && exit 1)
	$(VENV) -m src.cli track $(IMO)

track-all:
	$(VENV) -m src.cli track-all --limit $(or $(LIMIT),20)

export:
	$(VENV) -m src.cli export --output $(or $(OUTPUT),data/export.csv)

publish:
	bash scripts/publish_digest.sh

telegram-send:
	$(VENV) -c "from src.distribution.telegram import send_digest; send_digest()"

telegram-bot:
	$(VENV) -c "from src.distribution.telegram import run_bot; run_bot()"

sync-osintukraine:
	$(VENV) scripts/sync_osintukraine.py

map:
	$(VENV) -m src.cli map

site:
	$(VENV) -m src.cli site

discover:
	$(VENV) -m src.cli discover

daemon:
	$(VENV) -m src.cli daemon

daemon-start:
	nohup $(VENV) -m src.cli daemon > data/updater.log 2>&1 &
	@echo "Daemon started (PID in data/updater.pid)"

daemon-stop:
	@if [ -f data/updater.pid ]; then kill $$(cat data/updater.pid) 2>/dev/null && echo "Daemon stopped"; rm -f data/updater.pid; else echo "No daemon running"; fi

status:
	$(VENV) -m src.cli status

lookup:
	@test -n "$(IMO)" || (echo "Usage: make lookup IMO=1234567" && exit 1)
	$(VENV) -m src.cli lookup $(IMO)

clean:
	rm -rf .venv data/ __pycache__ .pytest_cache *.egg-info src/__pycache__ tests/__pycache__

docker-build:
	docker compose build

docker-ingest:
	docker compose run --rm ingest

docker-score:
	docker compose run --rm score

docker-digest:
	docker compose run --rm digest