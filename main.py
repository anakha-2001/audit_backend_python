import os
import asyncpg
from contextlib import asynccontextmanager
from pathlib import Path
import json

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from pydantic.types import Any

# ==============================================================================
# --- START OF AUTOMATIC DEBUGGING BLOCK ---
# ==============================================================================

# Load environment variables from .env file
dotenv_path = Path(__file__).parent / '.env'
load_dotenv(dotenv_path=dotenv_path)

# ==============================================================================
# --- END OF AUTOMATIC DEBUGGING BLOCK ---
# ==============================================================================


# --- Pydantic Models for Data Validation ---
class AuditCreate(BaseModel):
    audit_name: str = Field(..., alias='auditName')
    report_data: list[dict[str, Any]] = Field(..., alias='reportData')

db_pool = None

# --- Lifespan Manager for Startup and Shutdown Events ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    print("Connecting to the database using the credentials found above...")
    global db_pool
    try:
        db_pool = await asyncpg.create_pool(
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASSWORD"),
            database=os.getenv("DB_DATABASE"),
            host=os.getenv("DB_HOST"),
            port=int(os.getenv("DB_PORT", 5433))
        )
        app.state.pool = db_pool
        async with db_pool.acquire() as connection:
            await connection.execute("""
                CREATE TABLE IF NOT EXISTS audits (
                    id SERIAL PRIMARY KEY,
                    audit_name VARCHAR(255) NOT NULL,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    report_data JSONB
                );
            """)
        print("Database connection established and table 'audits' is ready.")
    except Exception as e:
        print(f"CRITICAL ERROR connecting to database: {e}")
    
    yield

    print("Closing database connection pool...")
    if db_pool:
        await db_pool.close()
    print("Shutdown complete.")


# Create the FastAPI app instance
app = FastAPI(lifespan=lifespan)


# --- Middleware ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- API ENDPOINTS ---
# --- API ENDPOINTS ---

@app.get("/api/audits")
async def get_all_audits():
    """ GET /api/audits """
    if not app.state.pool:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, 
            detail="Database connection pool is not available."
        )
    try:
        async with app.state.pool.acquire() as connection:
            rows = await connection.fetch('SELECT id, audit_name, created_at FROM audits ORDER BY created_at DESC')
            return [dict(row) for row in rows]
    except Exception as e:
        print(f"Error fetching audits: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error")

@app.get("/api/audits/{audit_id}")
async def get_audit_by_id(audit_id: int):
    """ GET /api/audits/:id """
    if not app.state.pool:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, 
            detail="Database connection pool is not available."
        )
    try:
        async with app.state.pool.acquire() as connection:
            row = await connection.fetchrow('SELECT * FROM audits WHERE id = $1', audit_id)
        if not row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Audit not found")
        return dict(row)
    except Exception as e:
        print(f"Error fetching audit {audit_id}: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error")

@app.post("/api/audits", status_code=status.HTTP_201_CREATED)
async def create_audit(audit: AuditCreate):
    """ POST /api/audits """
    if not app.state.pool:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, 
            detail="Database connection pool is not available."
        )
    query = """
        INSERT INTO audits (audit_name, report_data)
        VALUES ($1, $2)
        RETURNING id;
    """
    try:
        async with app.state.pool.acquire() as connection:
            # --- THIS IS THE FIX ---
            # Manually convert the Python list of dicts into a JSON string
            report_data_as_json_string = json.dumps(audit.report_data)
            
            # Now, pass the string to the database query
            new_audit_id = await connection.fetchval(
                query, 
                audit.audit_name, 
                report_data_as_json_string
            )
        return {"message": "Audit report saved successfully", "auditId": new_audit_id}
    except Exception as e:
        print(f"Error saving audit report: {e}")
        # Be more specific in the error detail for better frontend debugging
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Error saving to database: {e}")


# --- Production Logic: Serving Static Frontend Files ---
if os.getenv("NODE_ENV") == "production":
    frontend_build_path = Path(__file__).parent.parent / "frontend" / "build"
    app.mount("/static", StaticFiles(directory=frontend_build_path / "static"), name="static")
    @app.get("/{full_path:path}")
    async def serve_react_app(full_path: str):
        index_path = frontend_build_path / "index.html"
        if index_path.exists():
            from fastapi.responses import FileResponse
            return FileResponse(index_path)
        else:
            raise HTTPException(status_code=404, detail="Frontend entrypoint not found")