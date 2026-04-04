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
