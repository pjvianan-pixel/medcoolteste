# Medcoolteste

Telemedicine platform connecting patients with healthcare professionals for on-demand consultations.

## Project Structure

```
medcoolteste/
├── backend/          # FastAPI backend
│   ├── app/
│   │   ├── main.py          # Application entry point
│   │   ├── api/routes.py    # API routes
│   │   └── core/config.py   # Configuration
│   ├── tests/               # pytest tests
│   ├── .env.example         # Environment variable template
│   └── pyproject.toml       # Python project config (deps, ruff, mypy)
└── docs/
    ├── architecture.md      # System architecture & decisions
    └── domain.md            # Domain model & consultation flow
```

## Backend

### Prerequisites

- Python 3.11+
- PostgreSQL 15+

### Install dependencies

```bash
cd backend
pip install -e ".[dev]"
```

### Configure environment

```bash
cp backend/.env.example backend/.env
# Edit backend/.env with your local values
```

### Run database migrations

Make sure PostgreSQL is running and `DATABASE_URL` in `backend/.env` is correct, then:

```bash
cd backend
# Apply all pending migrations
alembic upgrade head

# (Optional) Roll back the last migration
alembic downgrade -1

# (Optional) Check current migration state
alembic current
```

> **Note:** `alembic` uses the `DATABASE_URL` from `backend/.env` (or the environment).
> The `+asyncpg` driver suffix is stripped automatically so that migrations use the
> synchronous `psycopg2` adapter. Install it with:
> ```bash
> pip install psycopg2-binary  # development only
> # or
> pip install psycopg2         # production
> ```

### Run backend

```bash
cd backend
uvicorn app.main:app --reload
```

The API will be available at <http://localhost:8000>.

Interactive docs: <http://localhost:8000/docs>

### Run tests

```bash
cd backend
pytest
```

### Lint

```bash
cd backend
ruff check .
```

### Type check

```bash
cd backend
mypy app
```

## Documentation

- [Architecture](docs/architecture.md)
- [Domain Model](docs/domain.md)
