
.PHONY: gen-ports verify verify-services-down verify-services-up

verify:
	@./scripts/verify.sh

verify-services-up:
	@docker compose -p hasanara-test -f docker-compose.test.yml up -d --wait

verify-services-down:
	@docker compose -p hasanara-test -f docker-compose.test.yml down --volumes --remove-orphans

gen-ports:
	@echo "Generating .env with random free host ports..."
	@python3 scripts/gen_ports.py
	@echo "Wrote .env -- start services with: docker compose up -d"
