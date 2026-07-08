import os
import sys
import subprocess

PROJECT_DIR = os.path.expanduser("~/projects/data-lab")
PYTHON = os.path.join(PROJECT_DIR, ".venv/bin/python")

PIPELINE_STEPS = [
    ("Load Dataset", "load_dataset.py"),
    ("Profile Dataset", "profile_dataset.py"),
    ("Generate Rules", "generate_rules.py"),
    ("Evaluate Rules", "evaluate_rules.py"),
    ("Generate Remediation Suggestions", "generate_remediation_suggestions.py"),
]


def run_step(step_name, script_name, dataset_name):
    script_path = os.path.join(PROJECT_DIR, "scripts", script_name)

    print(f"\n=== {step_name} ===")

    result = subprocess.run(
        [PYTHON, script_path, dataset_name],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
    )

    print(result.stdout)

    if result.stderr:
        print(result.stderr)

    if result.returncode != 0:
        raise RuntimeError(f"{step_name} failed.")


def main():
    if len(sys.argv) != 2:
        print("Usage: python run_pipeline.py <dataset_name>")
        sys.exit(1)

    dataset_name = sys.argv[1]

    print(f"Running pipeline for dataset: {dataset_name}")

    for step_name, script_name in PIPELINE_STEPS:
        run_step(step_name, script_name, dataset_name)

    print("\nPipeline complete.")


if __name__ == "__main__":
    main()