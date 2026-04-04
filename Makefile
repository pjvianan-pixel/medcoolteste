.PHONY: dev-backend test-backend lint-backend type-backend

dev-backend:
	cd backend && uvicorn app.main:app --reload

test-backend:
	cd backend && pytest

lint-backend:
	cd backend && ruff check .

type-backend:
	cd backend && mypy app
