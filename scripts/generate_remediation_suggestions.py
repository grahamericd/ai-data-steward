import os
import json
import re
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL



load_dotenv()

DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST")
DB_NAME = os.getenv("DB_NAME")


engine = create_engine(
    URL.create(
        "postgresql+psycopg2",
        username=DB_USER,
        password=DB_PASSWORD,
        host=DB_HOST,
        database=DB_NAME,
    )
 )

STATE_CODES = (
    "AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|IA|ID|IL|IN|KS|KY|LA|MA|MD|ME|MI|MN|MO|MS|MT|"
    "NC|ND|NE|NH|NJ|NM|NV|NY|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VA|VT|WA|WI|WV|WY"
)

CITY_STATE_PATTERN = re.compile(rf"^(?P<city>.+?)[,\s]+(?P<state>{STATE_CODES})\s*$")
CITY_ZIP_PATTERN = re.compile(r"^(?P<city>.+?)\s+(?P<zip>\d{5}(?:-\d{4})?)\s*$")


def is_blank(value):
    return value is None or str(value).strip() == ""


def propose_city_fix(row):
    city = (row.get("city") or "").strip()
    state = (row.get("state") or "").strip()
    zip_code = (row.get("zip") or "").strip()

    original_values = {
        "city": city,
        "state": state,
        "zip": zip_code,
    }

    match = CITY_STATE_PATTERN.match(city)
    if match and is_blank(state):
        return {
            "issue_type": "city_ends_with_state",
            "original_values": original_values,
            "suggested_values": {
                "city": match.group("city").strip().rstrip(","),
                "state": match.group("state").strip(),
                "zip": zip_code,
            },
            "confidence_score": 0.95,
        }

    match = CITY_ZIP_PATTERN.match(city)
    if match and is_blank(zip_code):
        return {
            "issue_type": "city_ends_with_zip",
            "original_values": original_values,
            "suggested_values": {
                "city": match.group("city").strip().rstrip(","),
                "state": state,
                "zip": match.group("zip").strip(),
            },
            "confidence_score": 0.90,
        }

    return None


def main():
    dataset_name = "corporate_data"
    source_table = "raw.corporate_data"

    with engine.begin() as conn:
        rows = conn.execute(
            text("""
                SELECT
                    corporation_number,
                    city,
                    state,
                    zip
                FROM raw.corporate_data
                WHERE city ~ ',\\s*[A-Z]{2}$'
                   OR city ~ '\\s+[A-Z]{2}\\s*$'
                   OR city ~ '\\s+[0-9]{5}(-[0-9]{4})?\\s*$'
            """)
        ).mappings().all()

        print(f"Candidate rows found: {len(rows)}")

        inserted = 0

        for row in rows:
            suggestion = propose_city_fix(dict(row))

            if suggestion is None:
                continue

            source_row_identifier = row["corporation_number"]

            # Avoid duplicate suggestions for same row + issue type
            exists = conn.execute(
                text("""
                    SELECT 1
                    FROM dq.remediation_suggestion
                    WHERE dataset_name = :dataset_name
                      AND source_row_identifier = :source_row_identifier
                      AND issue_type = :issue_type
                    LIMIT 1
                """),
                {
                    "dataset_name": dataset_name,
                    "source_row_identifier": source_row_identifier,
                    "issue_type": suggestion["issue_type"],
                }
            ).first()

            if exists:
                continue

            conn.execute(
                text("""
                    INSERT INTO dq.remediation_suggestion
                    (
                        dataset_name,
                        source_table,
                        rule_id,
                        source_row_identifier,
                        issue_type,
                        original_values,
                        suggested_values,
                        confidence_score,
                        status
                    )
                    VALUES
                    (
                        :dataset_name,
                        :source_table,
                        NULL,
                        :source_row_identifier,
                        :issue_type,
                        CAST(:original_values AS jsonb),
                        CAST(:suggested_values AS jsonb),
                        :confidence_score,
                        'proposed'
                    )
                """),
                {
                    "dataset_name": dataset_name,
                    "source_table": source_table,
                    "source_row_identifier": source_row_identifier,
                    "issue_type": suggestion["issue_type"],
                    "original_values": json.dumps(suggestion["original_values"]),
                    "suggested_values": json.dumps(suggestion["suggested_values"]),
                    "confidence_score": suggestion["confidence_score"],
                }
            )

            inserted += 1

        print(f"Remediation suggestions inserted: {inserted}")


if __name__ == "__main__":
    main()