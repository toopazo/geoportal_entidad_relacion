VENV   = .venv
PYTHON = $(VENV)/bin/python

# ── setup ─────────────────────────────────────────────────────────────────────

.PHONY: setup
setup: $(VENV)/.installed
	@echo "Venv listo. Dependencias instaladas."

$(VENV)/bin/python:
	python3 -m venv $(VENV)

# Reinstala si requirements.txt cambia
$(VENV)/.installed: scripts/requirements.txt $(VENV)/bin/python
	$(VENV)/bin/pip install --quiet -r scripts/requirements.txt
	touch $(VENV)/.installed

# ── postgres ──────────────────────────────────────────────────────────────────

.PHONY: db-up db-down db-reset
db-up:
	docker compose up -d postgres

db-down:
	docker compose stop postgres

db-reset:
	docker compose down -v postgres
	docker compose up -d postgres

# ── flujo de curado ───────────────────────────────────────────────────────────

# Uso: make load LAYER=establecimientos_salud
#      make load LAYER=establecimientos_salud ROWS=50
.PHONY: load
load: $(VENV)/.installed
ifndef LAYER
	$(error Falta LAYER. Uso: make load LAYER=establecimientos_salud)
endif
	$(PYTHON) scripts/load_sample.py --layer $(LAYER) $(if $(ROWS),--rows $(ROWS),)

# Uso: make schema-check
#      make schema-check LAYER=establecimientos_salud
#      make schema-check ARGS="--discover"
.PHONY: schema-check
schema-check: $(VENV)/.installed
	$(PYTHON) scripts/check_schema_drift.py $(if $(LAYER),--layer $(LAYER),) $(ARGS)

# Regenera el diagrama ER desde los YAMLs
.PHONY: build-er
build-er: $(VENV)/.installed
	$(PYTHON) scripts/build_er_model.py $(ARGS)

# ── scraping de catálogo ──────────────────────────────────────────────────────

# Uso: make scrape URL=https://geoportal.cl/geoportal/catalog/...
.PHONY: scrape
scrape: $(VENV)/.installed
ifndef URL
	$(error Falta URL. Uso: make scrape URL=https://geoportal.cl/geoportal/catalog/...)
endif
	$(PYTHON) scripts/scrape_catalog.py --url "$(URL)"

# ── perfilado de columnas ─────────────────────────────────────────────────────

# Uso: make profile LAYER=establecimientos_de_salud_de_chile_febrero_2026
.PHONY: profile
profile: $(VENV)/.installed
ifndef LAYER
	$(error Falta LAYER. Uso: make profile LAYER=...)
endif
	$(PYTHON) scripts/profile_layer.py --layer $(LAYER)

# ── ayuda ─────────────────────────────────────────────────────────────────────

.PHONY: help
help:
	@echo ""
	@echo "  make setup                              instala dependencias en .venv/"
	@echo "  make db-up                              levanta postgres (PostGIS)"
	@echo "  make db-down                            detiene postgres"
	@echo "  make db-reset                           borra datos y reinicia postgres"
	@echo ""
	@echo "  make scrape URL=<url_catalogo>           inicializa YAML desde página del catálogo"
	@echo "  make profile LAYER=<id>                  perfila columnas y genera stubs en el YAML"
	@echo "  make load LAYER=<id>                    descarga 100 filas y carga en postgres"
	@echo "  make load LAYER=<id> ROWS=50            ídem con N filas"
	@echo "  make schema-check                       verifica esquema de todas las capas"
	@echo "  make schema-check LAYER=<id>            ídem para una capa"
	@echo "  make schema-check ARGS='--discover'     solo capas pending_verification"
	@echo "  make build-er                           regenera diagrama ER"
	@echo ""
