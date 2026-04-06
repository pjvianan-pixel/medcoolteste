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

### Create the first admin user

After applying migrations, use the CLI script to create an admin:

```bash
cd backend
python scripts/create_admin.py --email admin@example.com --password yourpassword
```

The script reads `DATABASE_URL` from `backend/.env` (or the environment) and
creates a user with role `admin`. Run `alembic upgrade head` first to ensure
the `admin` value exists in the `user_role` enum.

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

## Twilio Video Setup (F3 Part 3)

The video consultation feature uses [Twilio Video](https://www.twilio.com/docs/video).
When the required credentials are absent the service falls back to stub values
so local development and tests work without a real Twilio account.

### Create credentials

1. Log in to the [Twilio Console](https://console.twilio.com).
2. Copy your **Account SID** (starts with `AC`).
3. Go to **Account › API Keys & Tokens** and create a new **Standard** API key.
   Copy the **SID** (starts with `SK`) and the **Secret** (shown once only).

### Configure environment

Add the following to `backend/.env`:

```ini
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_API_KEY=SKxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_API_SECRET=<your-api-secret>
TWILIO_VIDEO_ROOM_PREFIX=medcool-   # optional
```

### How it works

| Env vars set? | Behaviour |
|---|---|
| Yes | Real Twilio rooms created; JWT access tokens signed with your credentials |
| No  | Stub mode: deterministic mock values, no external calls |

Access tokens are short-lived JWTs issued per participant and per room.  Each
REST response that returns a `VideoSessionResponse` includes an `access_token`
field that the front-end passes to the Twilio Video JS SDK to join the room.
