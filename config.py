import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import URL


PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv()

DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST")
DB_NAME = os.getenv("DB_NAME")

OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")

RAW_DATA_DIR = Path(
    os.getenv("RAW_DATA_DIR", str(PROJECT_ROOT / "raw_data"))
).expanduser()

UPLOAD_DIR = Path(
    os.getenv(
        "UPLOAD_DIR",
        PROJECT_ROOT / "raw_data" 
    )
)

#UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
STAGING_SCHEMA = os.getenv("STAGING_SCHEMA", "staging")
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
SQL_DIR = PROJECT_ROOT / "sql"

engine = create_engine(
    URL.create(
        "postgresql+psycopg2",
        username=DB_USER,
        password=DB_PASSWORD,
        host=DB_HOST,
        database=DB_NAME,
    )
 )