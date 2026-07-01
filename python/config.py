import os
from dotenv import load_dotenv

load_dotenv()

DB_CONFIG = {
    "server":   os.getenv("DB_SERVER", "localhost"),
    "database": os.getenv("DB_NAME", "CRM_CustomerIntelligence"),
    "trusted_connection": os.getenv("DB_TRUSTED_CONNECTION", "yes"),
}

CONNECTION_STRING = (
    f"mssql+pyodbc://{DB_CONFIG['server']}/{DB_CONFIG['database']}"
    f"?driver=ODBC+Driver+17+for+SQL+Server"
    f"&trusted_connection={DB_CONFIG['trusted_connection']}"
)