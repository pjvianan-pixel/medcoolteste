# Architecture

## Overview

Medcoolteste is a telemedicine platform connecting patients with healthcare professionals for on-demand consultations.

## Component Overview

```
┌─────────────────────────────────────────────────────────┐
│                     Monorepo                            │
│                                                         │
│  ┌──────────────┐        ┌──────────────────────────┐  │
│  │   Mobile     │◄──────►│       Backend            │  │
│  │  (Flutter)   │  REST  │      (FastAPI)            │  │
│  └──────────────┘        └──────────┬───────────────┘  │
│                                     │                   │
│                           ┌─────────▼──────────┐        │
│                           │   Database         │        │
│                           │  (PostgreSQL)      │        │
│                           └────────────────────┘        │
│                                     │                   │
│                           ┌─────────▼──────────┐        │
│                           │   Real-time         │        │
│                           │  (WebSocket)        │        │
│                           └────────────────────┘        │
│                                     │                   │
│                           ┌─────────▼──────────┐        │
│                           │   Notifications     │        │
│                           │   (Push / FCM)      │        │
│                           └────────────────────┘        │
└─────────────────────────────────────────────────────────┘
```

### Components

| Component | Technology | Role |
|-----------|-----------|------|
| Mobile | Flutter | Cross-platform patient/professional app (iOS & Android) |
| Backend | FastAPI (Python) | REST API, business logic, authentication |
| Database | PostgreSQL | Persistent relational storage |
| Real-time | WebSocket | Live consultation session and messaging |
| Notifications | Push (FCM/APNs) | Appointment alerts, offer updates |

## Key Decisions

### Monorepo
All code (mobile, backend, shared docs) lives in a single repository. This simplifies dependency management, code sharing, and atomic cross-cutting changes during the early stages of the project.

### Flutter
Flutter enables a single codebase targeting both iOS and Android. Its widget model and strong typing reduce bugs and accelerate UI development for the MVP.

### FastAPI
FastAPI provides automatic OpenAPI documentation, async support, and type-safe request/response handling via Pydantic. It is well-suited for building a clean, documented REST API quickly.

### PostgreSQL
PostgreSQL offers a robust, open-source relational database with strong support for complex queries, JSON fields, and transactions — appropriate for medical record data and audit logging.

### WebSocket
WebSocket connections enable low-latency bidirectional communication required for live consultation sessions between patient and professional.

## Next Steps

### F1 – Authentication & Core Domain
- JWT-based authentication (register, login, refresh)
- User, PatientProfile and ProfessionalProfile CRUD
- Database migrations with Alembic

### F2 – Consultation Flow
- ConsultRequest / ConsultOffer matching engine
- ConsultSession lifecycle (WebSocket integration)
- Payment integration
- Push notification delivery
- Audit logging
