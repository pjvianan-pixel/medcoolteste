# Domain Model

## MVP Entities

### `user`
Central identity record. Stores authentication credentials and role (`patient` | `professional`).

| Field | Type | Description |
|-------|------|-------------|
| id | UUID | Primary key |
| email | string | Unique login email |
| hashed_password | string | bcrypt hash |
| role | enum | `patient` \| `professional` |
| is_active | bool | Soft-delete / suspension flag |
| created_at | timestamp | |

---

### `patient_profile`
Extended patient information linked 1-to-1 with `user`.

| Field | Type | Description |
|-------|------|-------------|
| id | UUID | Primary key |
| user_id | FK → user | |
| full_name | string | |
| date_of_birth | date | |
| cpf | string | Brazilian taxpayer ID (unique) |
| phone | string | |

---

### `professional_profile`
Healthcare professional details linked 1-to-1 with `user`.

| Field | Type | Description |
|-------|------|-------------|
| id | UUID | Primary key |
| user_id | FK → user | |
| full_name | string | |
| crm | string | Brazilian medical council registration |
| specialty | string | e.g. "General Practitioner" |
| bio | text | Short public description |
| is_available | bool | Availability toggle |

---

### `consult_request`
Created by a patient describing their complaint and opening a matching process.

| Field | Type | Description |
|-------|------|-------------|
| id | UUID | Primary key |
| patient_id | FK → patient_profile | |
| summary | text | Brief complaint description |
| desired_price | decimal | Patient's budget |
| status | enum | `open` \| `matched` \| `cancelled` |
| created_at | timestamp | |

---

### `consult_offer`
Created by a professional in response to a `consult_request`.

| Field | Type | Description |
|-------|------|-------------|
| id | UUID | Primary key |
| request_id | FK → consult_request | |
| professional_id | FK → professional_profile | |
| offered_price | decimal | Professional's counter-offer |
| message | text | Optional note to patient |
| status | enum | `pending` \| `accepted` \| `rejected` \| `expired` |
| created_at | timestamp | |

---

### `consult_session`
Active or past consultation linking patient, professional, and channel.

| Field | Type | Description |
|-------|------|-------------|
| id | UUID | Primary key |
| offer_id | FK → consult_offer | Accepted offer that originated this session |
| channel_id | string | WebSocket room / channel identifier |
| started_at | timestamp | |
| ended_at | timestamp | nullable |
| status | enum | `waiting` \| `active` \| `completed` \| `cancelled` |

---

### `payment`
Payment record associated with a consultation session.

| Field | Type | Description |
|-------|------|-------------|
| id | UUID | Primary key |
| session_id | FK → consult_session | |
| amount | decimal | Amount charged |
| currency | string | e.g. `BRL` |
| provider | string | Payment gateway (e.g. `stripe`, `pagseguro`) |
| provider_payment_id | string | External reference |
| status | enum | `pending` \| `paid` \| `failed` \| `refunded` |
| paid_at | timestamp | nullable |

---

### `audit_log`
Immutable log of security-sensitive and domain-critical events.

| Field | Type | Description |
|-------|------|-------------|
| id | UUID | Primary key |
| actor_id | FK → user | Who performed the action (nullable for system) |
| action | string | e.g. `session.started`, `payment.paid` |
| resource_type | string | Entity type (e.g. `consult_session`) |
| resource_id | UUID | Entity primary key |
| metadata | JSONB | Additional context |
| created_at | timestamp | |

---

## On-Demand Consultation Flow

```
Patient                                    Professional
  │                                             │
  │── 1. Submit complaint summary ──────────────│
  │   (consult_request: status=open)            │
  │                                             │
  │                         2. Receive request ─│
  │                         3. Send offer ──────│
  │                         (consult_offer:     │
  │                          offered_price,     │
  │                          status=pending)    │
  │                                             │
  │── 4. Review offer ───────────────────────── │
  │   (accept / reject / negotiate)             │
  │                                             │
  │── 5. Accept offer ───────────────────────── │
  │   (consult_offer: status=accepted)          │
  │                                             │
  │◄──────────── 6. Session created ────────────│
  │              (consult_session: status=waiting)
  │                                             │
  │◄════════════ 7. Live consultation ══════════│
  │              (WebSocket channel)            │
  │                                             │
  │◄──────────── 8. Session ended ──────────────│
  │              (consult_session: status=completed)
  │                                             │
  │── 9. Payment processed ─────────────────── │
  │   (payment: status=paid)                    │
  │                                             │
  │── 10. Audit logged ──────────────────────── │
      (audit_log entries throughout)
```

### Flow Summary

1. **Complaint** – Patient submits a brief complaint and budget (`consult_request`).
2. **Pricing** – Professional reviews the request and sends a price offer (`consult_offer`).
3. **Matching** – Platform surfaces matching professionals; patient chooses one.
4. **Counter-offer** – Patient may reject and negotiate; professional may revise offer.
5. **Consultation** – Accepted offer triggers session creation; real-time channel opens.
6. **Payment** – After session ends, payment is captured and recorded.
