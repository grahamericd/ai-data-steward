import os
import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine

#load_dotenv(os.path.expanduser("~/.datalab.env"))

#engine = create_engine(
#    f"postgresql+psycopg2://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}@{os.getenv('DB_HOST')}/{os.getenv('DB_NAME')}"
#)

engine = create_engine(
    "postgresql://egraham@localhost/florida_data_lab",
    connect_args={
        "password": "P@ssw0rd12345"
    }
)


FILE_PATH = "/home/eric-graham/projects/data-lab/raw_data/20260617c.txt"

fields = [
    ("corporation_number", 1, 12),
    ("corporation_name", 13, 192),
    ("status", 205, 1),
    ("filing_type", 206, 15),
    ("address_1", 221, 42),
    ("address_2", 263, 42),
    ("city", 305, 28),
    ("state", 333, 2),
    ("zip", 335, 10),
    ("country", 345, 2),
    ("mail_address_1", 347, 42),
    ("mail_address_2", 389, 42),
    ("mail_city", 431, 28),
    ("mail_state", 459, 2),
    ("mail_zip", 461, 10),
    ("mail_country", 471, 2),
    ("file_date", 473, 8),
    ("fei_number", 481, 14),
    ("more_than_six_officers_flag", 495, 1),
    ("last_transaction_date", 496, 8),
    ("state_country", 504, 2),
    ("report_year_1", 506, 4),
    ("report_date_1", 511, 8),
    ("report_year_2", 519, 4),
    ("report_date_2", 524, 8),
    ("report_year_3", 532, 4),
    ("report_date_3", 537, 8),
    ("registered_agent_name", 545, 42),
    ("registered_agent_type", 587, 1),
    ("registered_agent_address", 588, 42),
    ("registered_agent_city", 630, 28),
    ("registered_agent_state", 658, 2),
    ("registered_agent_zip4", 660, 9),
]

# Add 6 officer blocks
officer_starts = [669, 797, 925, 1053, 1181, 1309]

for i, start in enumerate(officer_starts, start=1):
    fields.extend([
        (f"officer_{i}_title", start, 4),
        (f"officer_{i}_type", start + 4, 1),
        (f"officer_{i}_name", start + 5, 42),
        (f"officer_{i}_address", start + 47, 42),
        (f"officer_{i}_city", start + 89, 28),
        (f"officer_{i}_state", start + 117, 2),
        (f"officer_{i}_zip4", start + 119, 9),
    ])

fields.append(("filler", 1437, 4))


def parse_record(line):
    row = {}
    for name, start, length in fields:
        zero_based = start - 1
        row[name] = line[zero_based:zero_based + length].replace("\x00", "").strip()
    return row


rows = []

with open(FILE_PATH, "r", encoding="latin-1", errors="replace") as f:
    for line in f:
        line = line.rstrip("\n")
        if not line.strip():
            continue
        rows.append(parse_record(line))

df = pd.DataFrame(rows)

print(f"Parsed rows: {len(df)}")
print(df.head())

df.to_sql(
    "corporate_data",
    engine,
    schema="raw",
    if_exists="replace",
    index=False
)

print("Loaded raw.corporate_data")