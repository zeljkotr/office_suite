from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.auth import router as auth_router
from app.api.invoices import router as invoices_router
from app.api.office import router as office_router
from app.api.email import router as email_router
from app.api.ai_email import router as ai_email_router
from app.api.ai_settings import router as ai_settings_router
from app.api.financial_records import router as financial_records_router
from app.api.tax import router as tax_router
from app.api.currency import router as currency_router
from app.api.accounting_periods import router as accounting_periods_router
from app.config import settings
from app.database import engine, SessionLocal
from app.models import Base
from app.models.accounting_period import ClosedAccountingPeriodError
from app.services.bootstrap import create_initial_admin
from app.api.receivables_payables import router as receivables_payables_router

app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description="Invoice management and office automation",
)

app.include_router(receivables_payables_router)

@app.exception_handler(ClosedAccountingPeriodError)
async def closed_accounting_period_handler(request, exc):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router, prefix="/api")
app.include_router(invoices_router, prefix="/api")
app.include_router(office_router, prefix="/api")
app.include_router(email_router, prefix="/api")
app.include_router(ai_email_router, prefix="/api")
app.include_router(ai_settings_router, prefix="/api")
app.include_router(financial_records_router, prefix="/api")
app.include_router(tax_router, prefix="/api")
app.include_router(currency_router, prefix="/api")
app.include_router(accounting_periods_router, prefix="/api")

static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.on_event("startup")
def startup():
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        import os
        if os.getenv("ADMIN_EMAIL") and os.getenv("ADMIN_PASSWORD"):
            create_initial_admin(db)


@app.get("/health")
def health():
    return {"status": "ok", "application": settings.app_name}


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(static_dir / "index.html")
