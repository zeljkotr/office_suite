# office_suite

**An integrated business management platform for finance, document processing, accounting, and workflow automation.**

Office Suite is a web-based business application designed to centralize everyday office and financial operations in one place. It combines invoice management, financial records, payment tracking, accounting periods, currency management, tax calculations, bank reconciliation, and AI-assisted document processing.

## Features

### Dashboard
- Centralized business overview
- Invoice statistics and financial summaries
- Accounts payable and receivable overview
- Recent activity and financial status

### Invoice Management
- Invoice upload and document processing
- AI-assisted invoice data extraction
- Invoice review and approval workflow
- Duplicate invoice detection
- Invoice downloads and document storage
- Incoming and outgoing invoice management

### Financial Management
- Income and expense records
- Financial transaction tracking
- Payment recording and allocation
- Paid, partially paid, and outstanding balances
- Accounts payable and receivable
- Financial reporting by currency and category

### Accounting Periods
- Create and manage accounting periods
- Close and reopen accounting periods
- Financial data snapshots
- Accounting period audit history
- Protection against unauthorized changes to closed periods

### Currency Management
- Configurable base currency
- Multiple currency support
- Exchange rate management
- Effective-date-based currency rates
- Currency-aware financial reporting

### Tax & Compliance
- Tax calculation engine
- Tax-related financial data
- Tax reporting functionality
- Support for financial compliance workflows

### Bank Reconciliation
- Bank reconciliation functionality
- Matching and reviewing financial transactions
- Reconciliation records and status tracking

### AI & Automation
- AI-assisted document extraction
- Automated invoice validation
- AI Email Manager
- Document workflow automation

## Technology Stack

| Component | Technology |
|---|---|
| Backend | Python, FastAPI |
| Database | PostgreSQL |
| ORM | SQLAlchemy |
| Database migrations | Alembic |
| Frontend | HTML, CSS, JavaScript |
| Testing | Pytest |
| Deployment | Docker Compose |

## Project Structure

```text
office_suite/
├── backend/
│   ├── app/
│   │   ├── api/
│   │   ├── models/
│   │   ├── schemas/
│   │   ├── services/
│   │   └── static/
│   ├── migrations/
│   ├── tests/
│   ├── alembic.ini
│   └── requirements.txt
├── docker-compose.yml
└── .gitignore
```

## Getting Started

### Requirements

- Python 3.10+
- PostgreSQL
- pip
- Docker and Docker Compose (optional)

### Configuration

Configure the application environment variables before starting the backend.

At minimum, configure:

- PostgreSQL connection URL
- Application security settings
- Authentication and encryption secrets
- AI provider settings, where applicable

Do not commit `.env` files, credentials, API keys, or production secrets to source control.

### Backend Installation

```bash
cd backend

python -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
```

Configure the database connection and apply migrations:

```bash
alembic upgrade head
```

Start the API:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## API Documentation

When the application is running, FastAPI provides interactive API documentation:

- Swagger UI: `http://localhost:8000/docs`
- OpenAPI schema: `http://localhost:8000/openapi.json`

## Running Tests

Run backend tests from the `backend` directory:

```bash
PYTHONPATH=. pytest -q
```

The test suite covers financial records, invoice workflows, currency accounting, bank reconciliation, reporting, and tax calculations.

**Database requirement:** PostgreSQL is the supported application database. Tests must not introduce SQLite as an alternative database backend.

## Database Migrations

Office Suite uses Alembic for database schema versioning.

Apply all migrations:

```bash
alembic upgrade head
```

Check the current migration revision:

```bash
alembic current
```

## Security

- Keep application secrets outside source control.
- Use strong database credentials.
- Protect authentication and encryption keys.
- Restrict access to uploaded documents.
- Use HTTPS in production.
- Back up the PostgreSQL database regularly.

## Development Status

Office Suite is under active development.

The project includes implemented invoice workflows, financial records, payment tracking, accounting periods, currency management, tax functionality, bank reconciliation, and financial reporting.

Further integration testing and feature development are ongoing.

## License

All rights reserved unless a license is explicitly included in this repository.

---

**Office Suite**  
*One platform for business operations, financial management, and office automation.*
