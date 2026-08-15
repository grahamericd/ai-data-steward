import argparse
import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import text

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import SCRIPTS_DIR, engine

PYTHON = sys.executable

PIPELINE_PHASES = [
    ("load", ["load_dataset.py"]),
    ("profiling", ["profile_dataset.py"]),
    (
        "rule_generation",
        [
            "generate_rules.py",
            "generate_multicolumn_rules.py",
            "generate_dataset_rules.py",
            "generate_reference_rules.py",
        ],
    ),
    ("evaluation", ["evaluate_rules.py"]),
    ("remediation", ["generate_remediation_suggestions.py"]),
]


def create_run(dataset_name, actor):
    with engine.begin() as conn:
        run_id = conn.execute(
            text("""
                INSERT INTO metadata.stewardship_run
                (dataset_id, dataset_name, initiated_by, status)
                SELECT dataset_id, dataset_name, :actor, 'running'
                FROM metadata.dataset
                WHERE dataset_name = :dataset_name AND active = TRUE
                RETURNING stewardship_run_id
            """),
            {"dataset_name": dataset_name, "actor": actor},
        ).scalar_one_or_none()
    if run_id is None:
        raise ValueError(f"Dataset not found or inactive: {dataset_name}")
    return int(run_id)


def start_phase(run_id, phase_name, actor):
    with engine.begin() as conn:
        return int(
            conn.execute(
                text("""
                    INSERT INTO metadata.stewardship_run_phase
                    (stewardship_run_id, phase_name, actor, status)
                    VALUES (:run_id, :phase_name, :actor, 'running')
                    RETURNING phase_id
                """),
                {"run_id": run_id, "phase_name": phase_name, "actor": actor},
            ).scalar_one()
        )


def finish_phase(phase_id, status, error_message=None, load_run_id=None):
    with engine.begin() as conn:
        conn.execute(
            text("""
                UPDATE metadata.stewardship_run_phase
                SET status = :status,
                    completed_at = CURRENT_TIMESTAMP,
                    error_message = :error_message,
                    load_run_id = COALESCE(:load_run_id, load_run_id)
                WHERE phase_id = :phase_id
            """),
            {
                "phase_id": phase_id,
                "status": status,
                "error_message": error_message,
                "load_run_id": load_run_id,
            },
        )


def finish_run(run_id, status, error_message=None):
    with engine.begin() as conn:
        conn.execute(
            text("""
                UPDATE metadata.stewardship_run
                SET status = :status,
                    completed_at = CURRENT_TIMESTAMP,
                    error_message = :error_message
                WHERE stewardship_run_id = :run_id
            """),
            {"run_id": run_id, "status": status, "error_message": error_message},
        )


def find_load_run_id(run_id):
    with engine.begin() as conn:
        value = conn.execute(
            text("""
                SELECT load_run_id
                FROM metadata.load_run
                WHERE stewardship_run_id = :run_id
                ORDER BY started_at DESC
                LIMIT 1
            """),
            {"run_id": run_id},
        ).scalar_one_or_none()
    return int(value) if value is not None else None


def run_script(script_name, dataset_name, run_id, actor):
    environment = os.environ.copy()
    environment["AI_STEWARD_RUN_ID"] = str(run_id)
    environment["AI_STEWARD_ACTOR"] = actor
    result = subprocess.run(
        [PYTHON, str(SCRIPTS_DIR / script_name), dataset_name],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        env=environment,
    )
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    if result.returncode != 0:
        raise RuntimeError(f"{script_name} failed: {result.stderr.strip()}")


def run_pipeline(dataset_name, actor):
    run_id = create_run(dataset_name, actor)
    print(f"Stewardship Run {run_id}: {dataset_name}")

    try:
        for phase_name, scripts in PIPELINE_PHASES:
            phase_id = start_phase(run_id, phase_name, actor)
            print(f"\n=== {phase_name.replace('_', ' ').title()} ===")
            try:
                for script_name in scripts:
                    run_script(script_name, dataset_name, run_id, actor)
                load_run_id = find_load_run_id(run_id) if phase_name == "load" else None
                finish_phase(phase_id, "completed", load_run_id=load_run_id)
            except Exception as exc:
                finish_phase(phase_id, "failed", error_message=str(exc))
                raise

        finish_run(run_id, "completed")
        print(f"\nStewardship Run {run_id} complete.")
        return run_id
    except Exception as exc:
        finish_run(run_id, "failed", error_message=str(exc))
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset_name")
    parser.add_argument("--actor", default=os.getenv("USER") or "cli-user")
    args = parser.parse_args()
    run_pipeline(args.dataset_name, args.actor.strip() or "cli-user")


if __name__ == "__main__":
    main()
