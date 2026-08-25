# infra

Local Docker development environment. This is for running the stack locally with a throwaway Postgres container — staging/production point at hosted Supabase Postgres instead.

## Prerequisites

- Docker Desktop (or compatible Docker engine + Compose v2)
- `backend/.env` and `web/.env.local` created from their respective `.env.example` files

## Usage

```bash
cd infra
docker compose up --build
```

- Backend: http://localhost:8000
- Web: http://localhost:3000
- Postgres: localhost:5432 (user/password/db: `postgres`)

## Conventions

- No Kubernetes, Redis, or message queues in the MVP. Add infrastructure only when a real, proven need shows up (see `/docs/decisions.md`).
- Each app owns its own `Dockerfile`; this directory only composes them for local use.
