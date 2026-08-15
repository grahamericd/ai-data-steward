import os
import pandas as pd
import streamlit as st
import sys
from pathlib import Path
from sqlalchemy import text
from sqlalchemy.engine import URL
import subprocess
import tempfile
import json
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parent
#PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
    
from config import RAW_DATA_DIR, engine

from scripts.load_dataset import (
    load_dataset,
    get_parser_definition,
    parse_fixed_width_record,
    quote_identifier,
)
from rule_approval import approve_rule, change_rule_status
from remediation_decision import change_remediation_status

st.set_page_config(
    page_title="AI Steward | Data Quality",
    page_icon="◆",
    layout="wide",
    initial_sidebar_state="expanded",
)

theme_path = PROJECT_ROOT / "assets" / "streamlit_theme.css"
st.markdown(
    f"<style>{theme_path.read_text(encoding='utf-8')}</style>",
    unsafe_allow_html=True,
)

st.sidebar.markdown(
    """
    <div class="steward-brand">
      <div class="steward-brand-mark">AI</div>
      <div>
        <div class="steward-brand-name">AI Steward</div>
        <div class="steward-brand-subtitle">Data quality service</div>
      </div>
    </div>
    """,
    unsafe_allow_html=True,
)

page = st.sidebar.radio(
    "Workspace",
    ["Dashboard", "Register Dataset", "Incremental Data Load","Dataset 360", "Steward Workbench", "Rule Catalog", "Failed Records", "Remediation Queue", "Raw Data Preview", "Run Pipeline"]
)

steward_identity = st.sidebar.text_input(
    "Steward identity",
    help="Required for approvals, rejections, and other governance decisions.",
).strip()
decision_note = st.sidebar.text_area(
    "Decision note (optional)",
)

page_descriptions = {
    "Dashboard": "A statewide view of data health, rule performance, and work requiring attention.",
    "Register Dataset": "Bring a governed source into the stewardship workflow.",
    "Incremental Data Load": "Validate and load new source records with traceable run history.",
    "Dataset 360": "Explore one dataset across metadata, quality, lineage, and remediation.",
    "Steward Workbench": "Review proposed rules and make accountable governance decisions.",
    "Rule Catalog": "Inspect rule contracts, provenance, status, and decision history.",
    "Failed Records": "Trace quality failures from evaluated rules to individual source rows.",
    "Remediation Queue": "Review safe corrections and their originating quality failures.",
    "Raw Data Preview": "Inspect a controlled sample of registered source data.",
    "Run Pipeline": "Coordinate profiling, rule generation, evaluation, and remediation.",
}

st.markdown(
    f"""
    <section class="steward-masthead">
      <div class="steward-eyebrow">Trusted data stewardship</div>
      <h1>{page}</h1>
      <p>{page_descriptions[page]}</p>
    </section>
    """,
    unsafe_allow_html=True,
)

def read_sql(query, params=None):
    return pd.read_sql(text(query), engine, params=params)
    
def get_dataset_registration(dataset_name):
    """
    Return the active metadata registration for one dataset.
    """

    dataset = read_sql(
        """
        SELECT *
        FROM metadata.dataset
        WHERE dataset_name = :dataset_name
          AND active = TRUE
        """,
        {
            "dataset_name": dataset_name,
        },
    )

    if dataset.empty:
        return None

    return dataset.iloc[0].to_dict()


def preview_fixed_width_file(
    dataset,
    file_path,
    max_rows=20,
):
    """
    Parse a small preview of a registered fixed-width file.
    """

    with engine.begin() as conn:
        fields = get_parser_definition(
            conn,
            dataset["dataset_id"],
        )

    if not fields:
        raise ValueError(
            "No fixed-width parser definition was found for "
            f"dataset '{dataset['dataset_name']}'."
        )

    rows = []

    with open(
        file_path,
        "r",
        encoding="latin-1",
        errors="replace",
    ) as source:
        for line in source:
            line = line.rstrip("\r\n")

            if not line.strip():
                continue

            rows.append(
                parse_fixed_width_record(
                    line,
                    fields,
                )
            )

            if len(rows) >= max_rows:
                break

    return pd.DataFrame(rows)


def preview_uploaded_file(
    dataset,
    file_path,
    max_rows=20,
):
    """
    Preview a file using the dataset's registered source type.
    """

    source_type = dataset["source_type"]

    if source_type == "csv":
        return pd.read_csv(
            file_path,
            dtype=str,
            keep_default_na=False,
            low_memory=False,
            nrows=max_rows,
        )

    if source_type == "fixed_width":
        return preview_fixed_width_file(
            dataset,
            file_path,
            max_rows=max_rows,
        )

    raise ValueError(
        f"Preview is not supported for source type '{source_type}'."
    )


def save_uploaded_file_temporarily(uploaded_file):
    """
    Write a Streamlit upload to a temporary local file.
    """

    file_suffix = Path(
        uploaded_file.name
    ).suffix

    with tempfile.NamedTemporaryFile(
        delete=False,
        suffix=file_suffix,
    ) as temporary_file:
        temporary_file.write(
            uploaded_file.getbuffer()
        )

        return Path(
            temporary_file.name
        )
    
     
def get_datasets():
    df = read_sql("""
        SELECT  dataset_name
        FROM metadata.dataset
        WHERE active = TRUE
        ORDER BY display_name, dataset_name
    """)
    return df["dataset_name"].tolist()
    
def run_script(script_name, *args):
    #project_dir = os.path.expanduser("~/projects/data-lab")
    project_dir = PROJECT_ROOT 
    python_path = os.path.join(project_dir, ".venv/bin/python")
    script_path = os.path.join(project_dir, "scripts", script_name)

    result = subprocess.run(
        [python_path, script_path, *args],
        cwd=project_dir,
        capture_output=True,
        text=True
    )

    return result
    
def format_last_loaded(timestamp_value):
    """
    Format a database timestamp for the Dataset 360 health banner.
    """

    if timestamp_value is None or pd.isna(timestamp_value):
        return "Never"

    timestamp = pd.Timestamp(timestamp_value)

    # PostgreSQL timestamps may already contain timezone information.
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")

    local_timestamp = timestamp.tz_convert(
        ZoneInfo("America/New_York")
    )

    now = pd.Timestamp.now(
        tz=ZoneInfo("America/New_York")
    )

    time_text = local_timestamp.strftime("%-I:%M %p")

    if local_timestamp.date() == now.date():
        return f"Today {time_text}"

    if (
        local_timestamp.date()
        == (now - pd.Timedelta(days=1)).date()
    ):
        return f"Yesterday {time_text}"

    return local_timestamp.strftime(
        "%b %-d, %Y %-I:%M %p"
    )
    
if page == "Dashboard":
    st.header("Data Quality Dashboard")
    
    kpi = read_sql("""
        SELECT
            (SELECT COUNT(DISTINCT dataset_name) FROM metadata.column_profile) AS datasets_loaded,
            (SELECT COUNT(*) FROM dq.rule) AS total_rules,
            (SELECT COUNT(*) FROM dq.rule WHERE status = 'approved') AS approved_rules,
            (SELECT COUNT(*) FROM dq.remediation_suggestion WHERE status = 'proposed') AS open_remediations
    """)

    col1, col2, col3, col4 = st.columns(4)

    col1.metric("Datasets Loaded", int(kpi.loc[0, "datasets_loaded"]))
    col2.metric("Total Rules", int(kpi.loc[0, "total_rules"]))
    col3.metric("Approved Rules", int(kpi.loc[0, "approved_rules"]))
    col4.metric("Open Remediations", int(kpi.loc[0, "open_remediations"]))

    results = read_sql("""
        SELECT
            r.dataset_name,
            COUNT(*) AS rule_runs,
            SUM(CASE WHEN r.result_status = 'PASS' THEN 1 ELSE 0 END) AS passed,
            SUM(CASE WHEN r.result_status = 'FAIL' THEN 1 ELSE 0 END) AS failed,
            SUM(CASE WHEN r.result_status = 'ERROR' THEN 1 ELSE 0 END) AS errors,
            SUM(r.failed_count) AS failed_records
        FROM dq.result r
        GROUP BY r.dataset_name
        ORDER BY r.dataset_name
    """)

    st.dataframe(results, use_container_width=True)

    st.subheader("Recent Results")

    recent = read_sql("""
        SELECT
            r.checked_at,
            r.dataset_name,
            ru.column_name,
            r.result_status,
            r.failed_count
        FROM dq.result r
        LEFT JOIN dq.rule ru ON r.rule_id = ru.id
        ORDER BY r.checked_at DESC
        LIMIT 25
    """)

    st.dataframe(recent, use_container_width=True)
    
elif page == "Incremental Data Load":

    st.header("Incremental Data Load")

    st.write(
        "Upload a new source file and load it into the dataset's "
        "registered raw table."
    )

    datasets = get_datasets()

    if not datasets:
        st.warning(
            "No active datasets are registered."
        )
        st.stop()

    dataset_name = st.selectbox(
        "Select Dataset",
        datasets,
        key="incremental_load_dataset",
    )

    dataset = get_dataset_registration(
        dataset_name
    )

    if dataset is None:
        st.error(
            "The selected dataset could not be found in the registry."
        )
        st.stop()

    source_type = dataset.get(
        "source_type"
    )

    raw_schema = dataset.get(
        "raw_schema"
    )

    raw_table = dataset.get(
        "raw_table"
    )

    primary_key = dataset.get(
        "primary_key"
    )

    registered_mode = (
        dataset.get("load_mode")
        or "upsert"
    ).lower()

    st.subheader("Dataset Configuration")

    col1, col2, col3, col4 = st.columns(4)

    col1.metric(
        "Source Type",
        source_type or "Not configured",
    )

    col2.metric(
        "Target Table",
        f"{raw_schema}.{raw_table}",
    )

    col3.metric(
        "Primary Key",
        primary_key or "Not configured",
    )

    col4.metric(
        "Default Load Mode",
        registered_mode,
    )

    if dataset.get("description"):
        st.info(
            dataset["description"]
        )

    st.divider()

    load_modes = [
        "upsert",
        "append",
        "replace",
    ]

    if registered_mode in load_modes:
        default_mode_index = load_modes.index(
            registered_mode
        )
    else:
        default_mode_index = 0

    load_mode = st.selectbox(
        "Load Mode",
        load_modes,
        index=default_mode_index,
        help=(
            "Upsert inserts new records and updates matching primary "
            "keys. Append adds every incoming row. Replace rebuilds "
            "the complete raw table."
        ),
    )

    if load_mode == "upsert" and not primary_key:
        st.error(
            "This dataset does not have a primary key configured. "
            "Upsert mode cannot be used."
        )

    if load_mode == "replace":
        st.warning(
            "Replace mode will delete and rebuild the existing raw table."
        )

    uploaded_file = st.file_uploader(
        "Choose Source File",
        key="incremental_source_file",
        help=(
            "The file will be parsed according to the dataset's "
            "registered source type and parser definition."
        ),
    )

    if uploaded_file is not None:

        file_col1, file_col2 = st.columns(2)

        file_col1.metric(
            "File Name",
            uploaded_file.name,
        )

        file_col2.metric(
            "File Size",
            f"{uploaded_file.size:,} bytes",
        )

        st.subheader("Parsed File Preview")

        preview_path = None

        try:
            preview_path = save_uploaded_file_temporarily(
                uploaded_file
            )

            preview = preview_uploaded_file(
                dataset,
                preview_path,
                max_rows=20,
            )

            st.dataframe(
                preview,
                use_container_width=True,
                hide_index=True,
            )

            preview_col1, preview_col2 = st.columns(2)

            preview_col1.metric(
                "Preview Rows",
                len(preview),
            )

            preview_col2.metric(
                "Detected Columns",
                len(preview.columns),
            )

            with st.expander(
                "Detected Column Names"
            ):
                st.write(
                    preview.columns.tolist()
                )

        except Exception as exc:
            st.error(
                f"Unable to preview the uploaded file: {exc}"
            )

        finally:
            if (
                preview_path is not None
                and preview_path.exists()
            ):
                preview_path.unlink(
                    missing_ok=True
                )

    st.divider()

    load_is_disabled = (
        uploaded_file is None
        or (
            load_mode == "upsert"
            and not primary_key
        )
    )

    if st.button(
        "Load File",
        type="primary",
        use_container_width=True,
        disabled=load_is_disabled,
    ):

        load_path = None

        try:
            load_path = save_uploaded_file_temporarily(
                uploaded_file
            )

            with st.spinner(
                "Parsing and loading the source file..."
            ):
                result = load_dataset(
                    dataset_name=dataset_name,
                    supplied_file=str(
                        load_path
                    ),
                    requested_mode=load_mode,
                    source_file_label=uploaded_file.name,
                    initiated_by=steward_identity or None,
                )

            st.success(
                "The source file loaded successfully."
            )

            result_col1, result_col2, result_col3 = st.columns(
                3
            )

            result_col1.metric(
                "Rows Received",
                result.get(
                    "rows_received",
                    0,
                ),
            )

            result_col2.metric(
                "Rows Inserted",
                result.get(
                    "rows_inserted",
                    0,
                ),
            )

            result_col3.metric(
                "Rows Updated",
                result.get(
                    "rows_updated",
                    0,
                ),
            )

            st.subheader("Load Summary")

            summary = pd.DataFrame(
                [
                    {
                        "Dataset": result.get(
                            "dataset_name"
                        ),
                        "Source File": uploaded_file.name,
                        "Source Type": result.get(
                            "source_type"
                        ),
                        "Load Mode": result.get(
                            "load_mode"
                        ),
                        "Primary Key": result.get(
                            "primary_key"
                        ),
                        "Target Table": result.get(
                            "target_table"
                        ),
                        "Status": result.get(
                            "status"
                        ),
                    }
                ]
            )

            st.dataframe(
                summary,
                use_container_width=True,
                hide_index=True,
            )

            with st.expander(
                "Technical Load Details"
            ):
                st.json(
                    result
                )

        except Exception as exc:
            st.error(
                f"Load failed: {exc}"
            )

        finally:
            if (
                load_path is not None
                and load_path.exists()
            ):
                load_path.unlink(
                    missing_ok=True
                )
                
        st.divider()
    st.subheader("Recent Load History")

    history_scope = st.radio(
        "History Scope",
        [
            "Selected Dataset",
            "All Datasets",
        ],
        horizontal=True,
        key="load_history_scope",
    )

    try:
        if history_scope == "Selected Dataset":
            load_history = read_sql(
                """
                SELECT
                    load_run_id,
                    started_at,
                    completed_at,
                    dataset_name,
                    source_file,
                    load_mode,
                    status,
                    rows_received,
                    rows_inserted,
                    rows_updated,
                    duration_seconds,
                    initiated_by,
                    error_message
                FROM metadata.load_run
                WHERE dataset_name = :dataset_name
                ORDER BY started_at DESC
                LIMIT 25
                """,
                {
                    "dataset_name": dataset_name,
                },
            )

        else:
            load_history = read_sql(
                """
                SELECT
                    load_run_id,
                    started_at,
                    completed_at,
                    dataset_name,
                    source_file,
                    load_mode,
                    status,
                    rows_received,
                    rows_inserted,
                    rows_updated,
                    duration_seconds,
                    initiated_by,
                    error_message
                FROM metadata.load_run
                ORDER BY started_at DESC
                LIMIT 25
                """
            )

        if load_history.empty:
            st.info(
                "No load-history records are available yet."
            )

        else:
            display_history = load_history.copy()

            display_history["status"] = (
                display_history["status"]
                .map(
                    {
                        "completed": "✅ Completed",
                        "failed": "❌ Failed",
                        "running": "⏳ Running",
                    }
                )
                .fillna(
                    display_history["status"]
                )
            )

            display_history["duration_seconds"] = (
                pd.to_numeric(
                    display_history["duration_seconds"],
                    errors="coerce",
                )
                .round(2)
            )

            st.dataframe(
                display_history[
                    [
                        "load_run_id",
                        "started_at",
                        "dataset_name",
                        "source_file",
                        "load_mode",
                        "status",
                        "rows_received",
                        "rows_inserted",
                        "rows_updated",
                        "duration_seconds",
                    ]
                ],
                use_container_width=True,
                hide_index=True,
            )

            failed_history = load_history[
                load_history["status"] == "failed"
            ]

            if not failed_history.empty:
                with st.expander(
                    "Recent Load Errors"
                ):
                    selected_failed_run = st.selectbox(
                        "Failed Load Run",
                        failed_history[
                            "load_run_id"
                        ].tolist(),
                        key="selected_failed_load_run",
                    )

                    failed_row = failed_history[
                        failed_history["load_run_id"]
                        == selected_failed_run
                    ].iloc[0]

                    st.error(
                        failed_row["error_message"]
                        or "No error message was recorded."
                    )

    except Exception as exc:
        st.error(
            f"Could not read load history: {exc}"
        )

elif page == "Steward Workbench":
    
    st.header("Steward Workbench")

    inbox = read_sql("""
        SELECT
            (SELECT COUNT(*) FROM dq.rule WHERE status = 'proposed') AS rules_awaiting_review,
            (SELECT COUNT(*) FROM dq.rule WHERE status = 'guardrail_rejected') AS guardrail_rejected,
            (SELECT COUNT(*) FROM dq.remediation_suggestion WHERE status = 'proposed') AS remediations_awaiting_review,
            (SELECT COUNT(*) FROM dq.result WHERE result_status = 'FAIL') AS failed_rule_results
    """)

    col1, col2, col3, col4 = st.columns(4)

    col1.metric("Rules Awaiting Review", int(inbox.loc[0, "rules_awaiting_review"]))
    col2.metric("Guardrail Rejected", int(inbox.loc[0, "guardrail_rejected"]))
    col3.metric("Remediations Awaiting Review", int(inbox.loc[0, "remediations_awaiting_review"]))
    col4.metric("Failed Rule Results", int(inbox.loc[0, "failed_rule_results"]))

    st.subheader("Rule Review Queue")

    rules = read_sql("""
        SELECT
            id,
            dataset_name,
            column_name,
            rule_type,
            status,
            rule_definition,
            created_at,
            llm_provider,
            llm_model,
            prompt_version,
            decision_by,
            decision_at
        FROM dq.rule
        WHERE status IN ('proposed', 'guardrail_rejected')
        ORDER BY created_at DESC
        LIMIT 50
    """)

    st.dataframe(rules, use_container_width=True)

    if not rules.empty:
        rule_id = st.selectbox("Select rule to review", rules["id"].tolist())

        selected_rule = rules[rules["id"] == rule_id].iloc[0]

        st.subheader("Selected Rule")
        st.write(f"Dataset: `{selected_rule['dataset_name']}`")
        st.write(f"Column: `{selected_rule['column_name']}`")
        st.write(f"Status: `{selected_rule['status']}`")
        st.write(
            "Provenance: "
            f"provider=`{selected_rule['llm_provider'] or 'system'}`, "
            f"model=`{selected_rule['llm_model'] or 'n/a'}`, "
            f"prompt=`{selected_rule['prompt_version'] or 'unknown'}`, "
            f"generated=`{selected_rule['created_at']}`"
        )
        if selected_rule["decision_by"]:
            st.write(
                f"Last decision: `{selected_rule['decision_by']}` at "
                f"`{selected_rule['decision_at']}`"
            )

        st.json(selected_rule["rule_definition"])

        col1, col2 = st.columns(2)

        with col1:
            if st.button("Approve Rule"):
                try:
                    with engine.begin() as conn:
                        canonical_type = approve_rule(
                            conn, rule_id, steward_identity, decision_note
                        )
                    st.success(
                        f"Rule {rule_id} approved as {canonical_type}."
                    )
                except ValueError as exc:
                    st.error(str(exc))

        with col2:
            if st.button("Reject Rule"):
                try:
                    with engine.begin() as conn:
                        change_rule_status(
                            conn, rule_id, "rejected",
                            steward_identity, decision_note,
                        )
                    st.warning(f"Rule {rule_id} rejected.")
                except ValueError as exc:
                    st.error(str(exc))
                
                
elif page == "Register Dataset":

    st.header("Register Dataset")

    with st.form("dataset_registration_form"):

        dataset_name = st.text_input("Dataset Name", placeholder="fictitious_names")
        display_name = st.text_input("Display Name", placeholder="Florida Fictitious Names")
        description = st.text_area("Description")
        source_type = st.selectbox("Source Type", ["csv", "fixed_width"])
        parser_name = st.text_input("Parser Name", value="fixed_width")
        source_file = st.text_input("Source File", placeholder="20260617f.txt")
        raw_schema = st.text_input("Raw Schema", value="raw")
        raw_table = st.text_input("Raw Table", placeholder="fictitious_names")
        primary_key = st.text_input("Primary Key", placeholder="document_number")
        active = st.checkbox("Active", value=True)

        submitted = st.form_submit_button("Register Dataset")

    if submitted:
        with engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO metadata.dataset
                    (
                        dataset_name,
                        display_name,
                        description,
                        source_type,
                        parser_name,
                        source_file,
                        raw_schema,
                        raw_table,
                        primary_key,
                        active
                    )
                    VALUES
                    (
                        :dataset_name,
                        :display_name,
                        :description,
                        :source_type,
                        :parser_name,
                        :source_file,
                        :raw_schema,
                        :raw_table,
                        :primary_key,
                        :active
                    )
                    ON CONFLICT (dataset_name)
                    DO UPDATE SET
                        display_name = EXCLUDED.display_name,
                        description = EXCLUDED.description,
                        source_type = EXCLUDED.source_type,
                        parser_name = EXCLUDED.parser_name,
                        source_file = EXCLUDED.source_file,
                        raw_schema = EXCLUDED.raw_schema,
                        raw_table = EXCLUDED.raw_table,
                        primary_key = EXCLUDED.primary_key,
                        active = EXCLUDED.active
                """),
                {
                    "dataset_name": dataset_name,
                    "display_name": display_name,
                    "description": description,
                    "source_type": source_type,
                    "parser_name": parser_name,
                    "source_file": source_file,
                    "raw_schema": raw_schema,
                    "raw_table": raw_table,
                    "primary_key": primary_key,
                    "active": active,
                }
            )

        st.success(f"Dataset `{dataset_name}` registered successfully.")
    st.divider()
    st.header("Register Fixed-Width Parser Definition")

    datasets_df = read_sql("""
        SELECT dataset_id, dataset_name, display_name
        FROM metadata.dataset
        WHERE source_type = 'fixed_width'
          AND active = TRUE
        ORDER BY dataset_name
    """)

    if datasets_df.empty:
        st.info("No active fixed-width datasets registered.")
    else:
        dataset_options = {
            f"{row['dataset_name']} - {row['display_name']}": row["dataset_id"]
            for _, row in datasets_df.iterrows()
        }

        selected_dataset_label = st.selectbox(
            "Select Fixed-Width Dataset",
            list(dataset_options.keys())
        )

        selected_dataset_id = dataset_options[selected_dataset_label]

        parser_text = st.text_area(
            "Paste Parser Definition",
            height=300,
            placeholder=(
                "column_name,start_position,field_length,sequence_number\n"
                "document_number,1,12,1\n"
                "fictitious_name,13,192,2\n"
                "county,205,12,3"
            )
        )

        replace_existing = st.checkbox(
            "Replace existing parser definition for this dataset",
            value=True
        )

        if st.button("Register Parser Definition"):

            lines = [
                line.strip()
                for line in parser_text.splitlines()
                if line.strip()
            ]

            if len(lines) <= 1:
                st.error("Paste a header row plus at least one parser row.")
            else:
                header = lines[0].split(",")

                expected_header = [
                    "column_name",
                    "start_position",
                    "field_length",
                    "sequence_number"
                ]

                if [h.strip() for h in header] != expected_header:
                    st.error(
                        "Header must be exactly: "
                        "column_name,start_position,field_length,sequence_number"
                    )
                else:
                    parser_rows = []

                    for line in lines[1:]:
                        parts = [p.strip() for p in line.split(",")]

                        if len(parts) != 4:
                            st.error(f"Invalid row: {line}")
                            st.stop()

                        parser_rows.append({
                            "dataset_id": selected_dataset_id,
                            "column_name": parts[0],
                            "start_position": int(parts[1]),
                            "field_length": int(parts[2]),
                            "sequence_number": int(parts[3]),
                        })

                    with engine.begin() as conn:

                        if replace_existing:
                            conn.execute(
                                text("""
                                    DELETE FROM metadata.parser_definition
                                    WHERE dataset_id = :dataset_id
                                """),
                                {"dataset_id": selected_dataset_id}
                            )

                        for row in parser_rows:
                            conn.execute(
                                text("""
                                    INSERT INTO metadata.parser_definition
                                    (
                                        dataset_id,
                                        column_name,
                                        start_position,
                                        field_length,
                                        sequence_number
                                    )
                                    VALUES
                                    (
                                        :dataset_id,
                                        :column_name,
                                        :start_position,
                                        :field_length,
                                        :sequence_number
                                    )
                                """),
                                row
                            )

                    st.success(
                        f"Registered {len(parser_rows)} parser fields."
                    )
        
    

elif page == "Dataset 360":
    st.header("Dataset 360")
    datasets = get_datasets()

    dataset_name = st.selectbox(
        "Select Dataset",
        datasets
    )

    dataset = read_sql("""
        SELECT *
        FROM metadata.dataset
        WHERE dataset_name = :dataset_name
    """, {"dataset_name": dataset_name})

    if dataset.empty:
        st.error("Dataset not found in registry.")
    else:
        dataset_row = dataset.iloc[0]

        st.subheader(dataset_row["display_name"] or dataset_name)

        # -------------------------------------------------------------
        # Dataset health status
        # -------------------------------------------------------------

        raw_schema = dataset_row["raw_schema"]
        raw_table = dataset_row["raw_table"]

        row_count = read_sql(
            f"""
            SELECT COUNT(*) AS row_count
            FROM "{raw_schema}"."{raw_table}"
            """
        )

        total_rows = int(
            row_count.loc[0, "row_count"] or 0
        )

        approved_rule_count = read_sql(
            """
            SELECT COUNT(*) AS approved_rules
            FROM dq.rule
            WHERE dataset_name = :dataset_name
              AND status = 'approved'
            """,
            {
                "dataset_name": dataset_name,
            },
        )

        approved_rules = int(
            approved_rule_count.loc[
                0,
                "approved_rules"
            ]
            or 0
        )

        pending_remediation_count = read_sql(
            """
            SELECT COUNT(*) AS pending_remediations
            FROM dq.remediation_suggestion
            WHERE dataset_name = :dataset_name
              AND status = 'proposed'
            """,
            {
                "dataset_name": dataset_name,
            },
        )

        pending_remediations = int(
            pending_remediation_count.loc[
                0,
                "pending_remediations"
            ]
            or 0
        )

        last_load = read_sql(
            """
            SELECT
                load_run_id,
                completed_at,
                source_file,
                rows_received,
                rows_inserted,
                rows_updated
            FROM metadata.load_run
            WHERE dataset_name = :dataset_name
              AND status = 'completed'
            ORDER BY completed_at DESC
            LIMIT 1
            """,
            {
                "dataset_name": dataset_name,
            },
        )

        if last_load.empty:
            last_loaded_text = "Never"
            last_load_run_id = None
        else:
            last_loaded_text = format_last_loaded(
                last_load.loc[0, "completed_at"]
            )

            last_load_run_id = int(
                last_load.loc[0, "load_run_id"]
            )

        # Use only the latest result for each approved rule.
        latest_quality_results = read_sql(
            """
            WITH latest_results AS
            (
                SELECT DISTINCT ON (r.rule_id)
                    r.rule_id,
                    r.result_status,
                    r.checked_at
                FROM dq.result r
                INNER JOIN dq.rule ru
                    ON ru.id = r.rule_id
                WHERE r.dataset_name = :dataset_name
                  AND ru.status = 'approved'
                ORDER BY
                    r.rule_id,
                    r.checked_at DESC
            )
            SELECT
                COUNT(*) AS evaluated_rules,
                SUM(
                    CASE
                        WHEN result_status = 'PASS'
                        THEN 1
                        ELSE 0
                    END
                ) AS passed_rules,
                SUM(
                    CASE
                        WHEN result_status = 'FAIL'
                        THEN 1
                        ELSE 0
                    END
                ) AS failed_rules
                ,SUM(
                    CASE
                        WHEN result_status = 'ERROR'
                        THEN 1
                        ELSE 0
                    END
                ) AS error_rules
            FROM latest_results
            """,
            {
                "dataset_name": dataset_name,
            },
        )

        evaluated_rules = int(
            latest_quality_results.loc[
                0,
                "evaluated_rules"
            ]
            or 0
        )

        passed_rules = int(
            latest_quality_results.loc[
                0,
                "passed_rules"
            ]
            or 0
        )

        failed_rules = int(
            latest_quality_results.loc[
                0,
                "failed_rules"
            ]
            or 0
        )

        error_rules = int(
            latest_quality_results.loc[0, "error_rules"]
            or 0
        )

        if evaluated_rules > 0:
            quality_score = round(
                (
                    passed_rules
                    / evaluated_rules
                )
                * 100,
                1,
            )
        else:
            quality_score = None

        # -------------------------------------------------------------
        # Determine dataset health
        # -------------------------------------------------------------

        if last_load.empty:
            health_label = "Not Loaded"
            health_icon = "⚪"
            health_message = (
                "This dataset has not completed a recorded load."
            )
            health_display = st.info

        elif quality_score is None:
            health_label = "Not Yet Assessed"
            health_icon = "🔵"
            health_message = (
                "The dataset is loaded, but its approved rules "
                "have not been evaluated."
            )
            health_display = st.info

        elif error_rules > 0:
            health_label = "Evaluation Error"
            health_icon = "🟠"
            health_message = (
                f"{error_rules} approved rule evaluation(s) failed. "
                "Review the result details before trusting the quality score."
            )
            health_display = st.error

        elif quality_score >= 95:
            health_label = "Healthy"
            health_icon = "🟢"
            health_message = (
                "The dataset is meeting its current approved "
                "data-quality expectations."
            )
            health_display = st.success

        elif quality_score >= 85:
            health_label = "Needs Attention"
            health_icon = "🟡"
            health_message = (
                "One or more approved quality rules are failing."
            )
            health_display = st.warning

        else:
            health_label = "At Risk"
            health_icon = "🔴"
            health_message = (
                "The dataset has a significant number of failed "
                "quality rules."
            )
            health_display = st.error

        st.markdown("### Dataset Health")

        health_display(
            f"{health_icon} **{health_label}** — "
            f"{health_message}"
        )

        health_col1, health_col2, health_col3, health_col4 = (
            st.columns(4)
        )

        health_col1.metric(
            "Quality Score",
            (
                f"{quality_score}%"
                if quality_score is not None
                else "Not assessed"
            ),
            help=(
                "Percentage of the latest approved-rule "
                "evaluations that passed."
            ),
        )

        health_col2.metric(
            "Last Loaded",
            last_loaded_text,
            help=(
                f"Latest successful load run: "
                f"{last_load_run_id or 'None'}"
            ),
        )

        health_col3.metric(
            "Rows",
            f"{total_rows:,}",
        )

        health_col4.metric(
            "Approved Rules",
            f"{approved_rules:,}",
        )

        remediation_col1, remediation_col2, remediation_col3 = (
            st.columns(3)
        )

        remediation_col1.metric(
            "Pending Remediations",
            f"{pending_remediations:,}",
        )

        remediation_col2.metric(
            "Evaluated Rules",
            f"{evaluated_rules:,}",
        )

        remediation_col3.metric(
            "Failed Rules",
            f"{failed_rules:,}",
        )

        if quality_score is not None:
            st.progress(
                quality_score / 100
            )

        st.divider()

        st.subheader("Stewardship Run History")
        stewardship_runs = read_sql(
            """
            SELECT stewardship_run_id, initiated_at, completed_at,
                   initiated_by, status, error_message
            FROM metadata.stewardship_run
            WHERE dataset_name = :dataset_name
            ORDER BY initiated_at DESC
            LIMIT 25
            """,
            {"dataset_name": dataset_name},
        )
        st.dataframe(stewardship_runs, use_container_width=True)

        if not stewardship_runs.empty:
            selected_run_id = st.selectbox(
                "Inspect stewardship run",
                stewardship_runs["stewardship_run_id"].tolist(),
            )
            run_phases = read_sql(
                """
                SELECT phase_name, status, actor, started_at,
                       completed_at, load_run_id, error_message
                FROM metadata.stewardship_run_phase
                WHERE stewardship_run_id = :run_id
                ORDER BY phase_id
                """,
                {"run_id": int(selected_run_id)},
            )
            st.markdown(f"#### Run {selected_run_id} Phases")
            st.dataframe(run_phases, use_container_width=True)

            run_artifacts = read_sql(
                """
                SELECT
                    (SELECT COUNT(*) FROM dq.rule
                     WHERE stewardship_run_id = :run_id) AS rules_generated,
                    (SELECT COUNT(*) FROM dq.result
                     WHERE stewardship_run_id = :run_id) AS rule_results,
                    (SELECT COUNT(*) FROM dq.failed_record fr
                     INNER JOIN dq.result r ON r.id = fr.result_id
                     WHERE r.stewardship_run_id = :run_id) AS failed_records,
                    (SELECT COUNT(*) FROM dq.remediation_suggestion
                     WHERE stewardship_run_id = :run_id) AS remediations
                """,
                {"run_id": int(selected_run_id)},
            )
            run_cols = st.columns(4)
            run_cols[0].metric("Rules Generated", int(run_artifacts.loc[0, "rules_generated"]))
            run_cols[1].metric("Rule Results", int(run_artifacts.loc[0, "rule_results"]))
            run_cols[2].metric("Failed Records", int(run_artifacts.loc[0, "failed_records"]))
            run_cols[3].metric("Remediations", int(run_artifacts.loc[0, "remediations"]))

        st.divider()

        st.markdown("### Dataset Details")
        
        tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(["Overview", "Columns", "Rules", "Quality", "Remediation", "Raw vs Curated"])

    with tab1:
        st.markdown("### Dataset Details")

        st.write(f"**Dataset Name:** `{dataset_row['dataset_name']}`")
        st.write(f"**Source Type:** `{dataset_row['source_type']}`")
        st.write(f"**Parser:** `{dataset_row['parser_name']}`")
        st.write(f"**Source File:** `{dataset_row['source_file']}`")
        st.write(f"**Raw Table:** `{dataset_row['raw_schema']}.{dataset_row['raw_table']}`")
        st.write(f"**Primary Key:** `{dataset_row['primary_key']}`")
        st.write(f"**Description:** {dataset_row['description']}")

    with tab2:
        st.markdown("### Column Catalog")

        columns = read_sql("""
            SELECT
                column_name,
                inferred_type,
                row_count,
                null_count,
                null_percent,
                distinct_count,
                min_value,
                max_value,
                sample_values
            FROM metadata.column_profile
            WHERE dataset_name = :dataset_name
            ORDER BY column_name
        """, {"dataset_name": dataset_name})

        st.dataframe(columns, use_container_width=True)

        if not columns.empty:
            selected_column = st.selectbox(
                "Select column",
                columns["column_name"].tolist()
            )

            col_profile = columns[
                columns["column_name"] == selected_column
            ].iloc[0]

            st.subheader(f"Column: {selected_column}")

            error_runs = total_runs - passed_runs - failed_runs
            c1, c2, c3, c4, c5 = st.columns(5)

            c1.metric("Type", col_profile["inferred_type"])
            c2.metric("Null %", round(float(col_profile["null_percent"]), 2))
            c3.metric("Distinct", int(col_profile["distinct_count"]))
            c4.metric("Rows", int(col_profile["row_count"]))

            st.markdown("#### Sample Values")
            st.json(col_profile["sample_values"])

    with tab3:
        st.markdown("### Rule Catalog")
        
        rules = read_sql("""
            SELECT
                id,
                dataset_name,
                column_name,
                rule_type,
                rule_scope,
                target_columns,
                status,
                created_at,
                rule_definition
            FROM dq.rule
            ORDER BY created_at DESC
        """, {"dataset_name": dataset_name})

        # rules = read_sql("""
            # SELECT
                # id,
                # column_name,
                # rule_type,
                # status,
                # created_at,
                # rule_definition
            # FROM dq.rule
            # WHERE dataset_name = :dataset_name
            # ORDER BY created_at DESC
        # """, {"dataset_name": dataset_name})

        st.dataframe(rules, use_container_width=True)

        if not rules.empty:
            selected_rule = st.selectbox(
                "Select rule",
                rules["id"].tolist()
            )

            rule_row = rules[
                rules["id"] == selected_rule
            ].iloc[0]

            st.subheader(f"Rule {selected_rule}")

            c1, c2, c3 = st.columns(3)

            c1.metric("Column", rule_row["column_name"])
            c2.metric("Type", rule_row["rule_type"])
            c3.metric("Status", rule_row["status"])

            st.markdown("#### Rule Definition")
            st.json(rule_row["rule_definition"])  
        
    with tab4:
        st.markdown("### Data Quality Score")

        quality = read_sql("""
            SELECT
                COUNT(*) AS total_runs,
                SUM(CASE WHEN result_status = 'PASS' THEN 1 ELSE 0 END) AS passed_runs,
                SUM(CASE WHEN result_status = 'FAIL' THEN 1 ELSE 0 END) AS failed_runs,
                SUM(failed_count) AS failed_records
            FROM dq.result
            WHERE dataset_name = :dataset_name
        """, {"dataset_name": dataset_name})

        total_runs = int(quality.loc[0, "total_runs"] or 0)
        passed_runs = int(quality.loc[0, "passed_runs"] or 0)
        failed_runs = int(quality.loc[0, "failed_runs"] or 0)
        failed_records = int(quality.loc[0, "failed_records"] or 0)

        if total_runs == 0:
            st.info("No rule evaluation results yet.")
        else:
            score = round((passed_runs / total_runs) * 100, 2)

            c1, c2, c3, c4 = st.columns(4)

            c1.metric("Quality Score", f"{score}%")
            c2.metric("Rule Runs", total_runs)
            c3.metric("Passed", passed_runs)
            c4.metric("Failed", failed_runs)
            c5.metric("Errors", error_runs)

            st.progress(score / 100)

            st.markdown("### Failed Records")
            st.metric("Total Failed Records", failed_records)

            results = read_sql("""
                SELECT
                    r.checked_at,
                    ru.column_name,
                    ru.rule_type,
                    r.result_status,
                    r.failed_count,
                    r.details
                FROM dq.result r
                LEFT JOIN dq.rule ru
                    ON r.rule_id = ru.id
                WHERE r.dataset_name = :dataset_name
                ORDER BY r.checked_at DESC
            """, {"dataset_name": dataset_name})

            st.dataframe(results, use_container_width=True)
        
        
    with tab5:
        st.markdown("### Remediation Summary")

        summary = read_sql("""
            SELECT
                status,
                COUNT(*) AS count
            FROM dq.remediation_suggestion
            WHERE dataset_name = :dataset_name
            GROUP BY status
            ORDER BY status
        """, {"dataset_name": dataset_name})

        st.dataframe(summary, use_container_width=True)

        remediations = read_sql("""
            SELECT
                id,
                source_row_identifier,
                issue_type,
                original_values,
                suggested_values,
                confidence_score,
                status,
                created_at,
                approved_by,
                approved_at
            FROM dq.remediation_suggestion
            WHERE dataset_name = :dataset_name
            ORDER BY created_at DESC
            LIMIT 100
        """, {"dataset_name": dataset_name})

        st.markdown("### Remediation Suggestions")
        st.dataframe(remediations, use_container_width=True)

        if not remediations.empty:
            selected_id = st.selectbox(
                "Select remediation",
                remediations["id"].tolist()
            )

            row = remediations[
                remediations["id"] == selected_id
            ].iloc[0]

            st.subheader(f"Remediation {selected_id}")

            c1, c2, c3 = st.columns(3)
            c1.metric("Issue Type", row["issue_type"])
            c2.metric("Confidence", row["confidence_score"])
            c3.metric("Status", row["status"])

            st.markdown("#### Original Values")
            st.json(row["original_values"])

            st.markdown("#### Suggested Values")
            st.json(row["suggested_values"])
       

    with tab6:
        st.markdown("### Curated Version History")

        primary_key = dataset_row["primary_key"]

        if not primary_key:
            st.info("This dataset does not have a primary key configured.")
        else:
            versions = read_sql("""
                SELECT curated_version_id, previous_version_id,
                       remediation_run_id, stewardship_run_id,
                       physical_table_name, created_by, created_at, row_count
                FROM curated.dataset_version
                WHERE dataset_name = :dataset_name AND status = 'completed'
                ORDER BY curated_version_id DESC
            """, {"dataset_name": dataset_name})

            if versions.empty:
                st.info("No curated version exists yet. Apply approved remediations first.")
            else:
                st.dataframe(versions, use_container_width=True, hide_index=True)
                version_ids = versions["curated_version_id"].astype(int).tolist()
                selected_version = st.selectbox(
                    "Inspect curated version",
                    version_ids,
                    format_func=lambda value: f"Version {value}",
                )
                version = versions.loc[
                    versions["curated_version_id"] == selected_version
                ].iloc[0]
                physical_table = quote_identifier(version["physical_table_name"])
                raw_schema = quote_identifier(dataset_row["raw_schema"])
                raw_table = quote_identifier(dataset_row["raw_table"])
                quoted_primary_key = quote_identifier(primary_key)
                previous_version = (
                    int(version["previous_version_id"])
                    if pd.notna(version["previous_version_id"])
                    else "none"
                )
                st.caption(
                    f"Produced by remediation run {int(version['remediation_run_id'])}; "
                    f"previous version: {previous_version}."
                )
                keys = read_sql(f"""
                    SELECT {quoted_primary_key} AS key_value
                    FROM {raw_schema}.{raw_table}
                    LIMIT 100
                """)

                selected_key = st.selectbox(
                    "Select record",
                    keys["key_value"].tolist()
                )

                raw_record = read_sql(f"""
                    SELECT *
                    FROM {raw_schema}.{raw_table}
                    WHERE {quoted_primary_key} = :key_value
                    LIMIT 1
                """, {"key_value": selected_key})

                curated_record = read_sql(f"""
                    SELECT *
                    FROM curated.{physical_table}
                    WHERE {quoted_primary_key} = :key_value
                    LIMIT 1
                """, {"key_value": selected_key})

                col1, col2 = st.columns(2)

                with col1:
                    st.subheader("Raw")
                    st.dataframe(raw_record, use_container_width=True)

                with col2:
                    st.subheader("Curated")
                    st.dataframe(curated_record, use_container_width=True)

                st.markdown("### Raw Row Lineage")
                lineage = read_sql("""
                    SELECT source_row_identifier, raw_load_run_id,
                           source_file, created_at
                    FROM curated.row_lineage
                    WHERE curated_version_id = :version_id
                      AND source_row_identifier = :key_value
                """, {
                    "version_id": selected_version,
                    "key_value": selected_key
                })
                st.dataframe(lineage, use_container_width=True, hide_index=True)

                st.markdown("### Changes in This Version")
                changes = read_sql("""
                    SELECT ch.change_id, ch.source_row_identifier,
                           ch.column_name, ch.previous_value, ch.new_value,
                           ch.remediation_id, ch.rule_id, r.rule_type,
                           ch.result_id, ch.failed_record_id, ch.changed_at
                    FROM curated.change_history ch
                    JOIN dq.rule r ON r.id = ch.rule_id
                    WHERE ch.curated_version_id = :version_id
                    ORDER BY ch.change_id
                """, {"version_id": selected_version})
                st.dataframe(changes, use_container_width=True, hide_index=True)
        # st.write(f"**Dataset Name:** `{dataset_row['dataset_name']}`")
        # st.write(f"**Source Type:** `{dataset_row['source_type']}`")
        # st.write(f"**Parser:** `{dataset_row['parser_name']}`")
        # st.write(f"**Source File:** `{dataset_row['source_file']}`")
        # st.write(f"**Raw Table:** `{dataset_row['raw_schema']}.{dataset_row['raw_table']}`")
        # st.write(f"**Primary Key:** `{dataset_row['primary_key']}`")
        # st.write(f"**Description:** {dataset_row['description']}")

elif page == "Rule Catalog":
    st.header("Rule Catalog")

    rules = read_sql("""
        SELECT
            id,
            dataset_name,
            column_name,
            rule_type,
            status,
            created_at,
            llm_provider,
            llm_model,
            prompt_version,
            decision_by,
            decision_at,
            rule_definition
        FROM dq.rule
        ORDER BY created_at DESC
    """)

    st.dataframe(rules, use_container_width=True)

    st.subheader("Update Rule Status")

    rule_id = st.number_input("Rule ID", min_value=1, step=1)
    new_status = st.selectbox("New Status", ["proposed", "approved", "rejected", "retired"])

    if st.button("Update Rule"):
        try:
            with engine.begin() as conn:
                change_rule_status(
                    conn, int(rule_id), new_status,
                    steward_identity, decision_note,
                )
            st.success(f"Rule {rule_id} updated to {new_status}")
        except ValueError as exc:
            st.error(str(exc))

    rule_history = read_sql(
        """
        SELECT previous_status, new_status, changed_by, changed_at, decision_note
        FROM dq.rule_audit
        WHERE rule_id = :rule_id
        ORDER BY changed_at DESC, id DESC
        """,
        {"rule_id": int(rule_id)},
    )
    st.subheader("Rule Decision History")
    st.dataframe(rule_history, use_container_width=True)

elif page == "Failed Records":
    st.header("Failed Records")

    failed_records = read_sql("""
        SELECT
            fr.id AS failed_record_id,
            fr.result_id,
            fr.created_at,
            fr.dataset_name,
            fr.source_row_identifier,
            fr.rule_id,
            ru.column_name,
            ru.rule_type,
            r.checked_at
        FROM dq.failed_record fr
        INNER JOIN dq.result r ON r.id = fr.result_id
        LEFT JOIN dq.rule ru ON ru.id = fr.rule_id
        ORDER BY fr.created_at DESC, fr.id DESC
    """)

    if failed_records.empty:
        st.success("No failed records found.")
    else:
        st.dataframe(failed_records, use_container_width=True)

        result_id = st.selectbox(
            "Select failed result",
            failed_records["result_id"].drop_duplicates().tolist()
        )

        selected_records = failed_records[
            failed_records["result_id"] == result_id
        ]
        selected = selected_records.iloc[0]

        st.subheader("Failure Summary")
        st.write(f"Dataset: `{selected['dataset_name']}`")
        st.write(f"Column: `{selected['column_name']}`")
        st.write(f"Rule: `{selected['rule_id']}`")
        st.write(f"Failed Records: `{len(selected_records)}`")

        st.subheader("All Failed Row Identifiers")
        st.dataframe(
            selected_records[
                ["failed_record_id", "source_row_identifier", "created_at"]
            ],
            use_container_width=True,
        )


elif page == "Remediation Queue":

    st.header("Remediation Queue")

    status_filter = st.selectbox(
        "Status",
        ["proposed", "approved", "rejected", "applied", "all"]
    )

    if status_filter == "all":
        query = """
            SELECT
                remediation.*,
                rule.rule_type AS originating_rule_type,
                rule.column_name AS originating_column,
                failed.source_row_identifier AS failed_row_identifier,
                result.checked_at AS originating_evaluation_at
            FROM dq.remediation_suggestion remediation
            INNER JOIN dq.rule rule ON rule.id = remediation.rule_id
            INNER JOIN dq.result result ON result.id = remediation.result_id
            INNER JOIN dq.failed_record failed
                ON failed.id = remediation.failed_record_id
            ORDER BY remediation.created_at DESC
            LIMIT 100
        """
        remediations = read_sql(query)

    else:
        query = """
            SELECT
                remediation.*,
                rule.rule_type AS originating_rule_type,
                rule.column_name AS originating_column,
                failed.source_row_identifier AS failed_row_identifier,
                result.checked_at AS originating_evaluation_at
            FROM dq.remediation_suggestion remediation
            INNER JOIN dq.rule rule ON rule.id = remediation.rule_id
            INNER JOIN dq.result result ON result.id = remediation.result_id
            INNER JOIN dq.failed_record failed
                ON failed.id = remediation.failed_record_id
            WHERE remediation.status = :status
            ORDER BY remediation.created_at DESC
            LIMIT 100
        """
        remediations = read_sql(
            query,
            {"status": status_filter}
        )

    st.dataframe(
        remediations,
        use_container_width=True
    )

    if not remediations.empty:

        remediation_id = st.selectbox(
            "Select remediation to review",
            remediations["id"].tolist()
        )

        selected = remediations[
            remediations["id"] == remediation_id
        ].iloc[0]

        st.subheader("Originating Failure")
        st.write(f"Rule: `{selected['rule_id']}` — `{selected['originating_rule_type']}`")
        st.write(f"Result: `{selected['result_id']}`")
        st.write(f"Failed record: `{selected['failed_record_id']}`")
        st.write(f"Source row: `{selected['failed_row_identifier']}`")
        st.write(f"Generation: `{selected['generation_method']}`")

        st.subheader("Original Values")
        st.json(selected["original_values"])

        st.subheader("Suggested Values")
        st.json(selected["suggested_values"])

        st.metric(
            "Confidence Score",
            selected["confidence_score"]
        )

        col1, col2 = st.columns(2)

        with col1:

            if st.button("Approve Suggestion"):

                try:
                    with engine.begin() as conn:
                        change_remediation_status(
                            conn, remediation_id, "approved",
                            steward_identity, decision_note,
                        )
                    st.success(f"Remediation {remediation_id} approved.")
                except ValueError as exc:
                    st.error(str(exc))

        with col2:

            if st.button("Reject Suggestion"):

                try:
                    with engine.begin() as conn:
                        change_remediation_status(
                            conn, remediation_id, "rejected",
                            steward_identity, decision_note,
                        )
                    st.warning(f"Remediation {remediation_id} rejected.")
                except ValueError as exc:
                    st.error(str(exc))

        remediation_history = read_sql(
            """
            SELECT previous_status, new_status, changed_by, changed_at, decision_note
            FROM dq.remediation_audit
            WHERE remediation_id = :remediation_id
            ORDER BY changed_at DESC, id DESC
            """,
            {"remediation_id": int(remediation_id)},
        )
        st.subheader("Remediation Decision History")
        st.dataframe(remediation_history, use_container_width=True)

    else:

        st.info(
            "No remediation suggestions found."
        )
    
  

    # st.subheader("Approve / Reject Suggestion")

    # remediation_id = st.number_input("Remediation ID", min_value=1, step=1)
    # decision = st.selectbox("Decision", ["approved", "rejected"])

    # if st.button("Submit Decision"):
        # with engine.begin() as conn:
            # conn.execute(
                # text("""
                    # UPDATE dq.remediation_suggestion
                    # SET status = :status,
                        # approved_by = :steward_identity,
                        # approved_at = now()
                    # WHERE id = :id
                # """),
                # {"status": decision, "id": remediation_id}
            #)
        #st.success(f"Remediation {remediation_id} marked as {decision}")

# elif page == "Run Pipeline":
    # st.header("Run Pipeline Tasks")

    # if st.button("Run Profiler"):
        # result = run_script("profile_dataset.py")
        # st.code(result.stdout)
        # if result.stderr:
            # st.error(result.stderr)
            
    # if st.button("Generate Rules with Ollama"):
        # result = run_script("generate_rules.py")
        # st.code(result.stdout)
        # if result.stderr:
            # st.error(result.stderr)

    # if st.button("Evaluate Approved Rules"):
        # result = run_script("evaluate_rules.py")
        # st.code(result.stdout)
        # if result.stderr:
            # st.error(result.stderr)

    # if st.button("Generate Remediation Suggestions"):
        # result = run_script("generate_remediation_suggestions.py")
        # st.code(result.stdout)
        # if result.stderr:
            # st.error(result.stderr)
            
elif page == "Run Pipeline":

    st.header("Run Data Quality Pipeline")

    datasets = get_datasets()

    dataset_name = st.selectbox(
        "Select Dataset",
        datasets
    )

    st.write(
        f"Selected dataset: `{dataset_name}`"
    )

    # =========================================================
    # BUSINESS RULE ANALYSIS
    # =========================================================

    st.markdown("### Business Rule Analysis")

    st.write(
        "Generate quality rules at the column, "
        "multi-column, or dataset level."
    )

    rule_col1, rule_col2, rule_col3 = st.columns(3)

    # ---------------------------------------------------------
    # Column Rules
    # ---------------------------------------------------------

    with rule_col1:

        if st.button(
            "Generate Column Rules",
            use_container_width=True,
            key="generate_column_rules"
        ):

            with st.spinner(
                "Generating column-level rules..."
            ):

                result = run_script(
                    "generate_rules.py",
                    dataset_name
                )

            st.subheader(
                "Column Rule Generation Output"
            )

            st.code(
                result.stdout
            )

            if result.stderr:
                st.subheader(
                    "Errors / Warnings"
                )

                st.error(
                    result.stderr
                )

            if result.returncode == 0:
                st.success(
                    "Column rule generation completed successfully."
                )
            else:
                st.error(
                    "Column rule generation failed."
                )

    # ---------------------------------------------------------
    # Multi-Column / Row Rules
    # ---------------------------------------------------------

    with rule_col2:

        if st.button(
            "Generate Multi-Column Rules",
            use_container_width=True,
            key="generate_multicolumn_rules"
        ):

            with st.spinner(
                "Analyzing relationships between columns. "
                "This may take several minutes..."
            ):

                result = run_script(
                    "generate_multicolumn_rules.py",
                    dataset_name
                )

            st.subheader(
                "Multi-Column Rule Output"
            )

            st.code(
                result.stdout
            )

            if result.stderr:
                st.subheader(
                    "Errors / Warnings"
                )

                st.error(
                    result.stderr
                )

            if result.returncode == 0:
                st.success(
                    "Multi-column analysis completed successfully."
                )
            else:
                st.error(
                    "Multi-column analysis failed."
                )

    # ---------------------------------------------------------
    # Dataset Rules
    # ---------------------------------------------------------

    with rule_col3:

        if st.button(
            "Generate Dataset Rules",
            use_container_width=True,
            key="generate_dataset_rules"
        ):

            with st.spinner(
                "Generating dataset-level rules..."
            ):

                result = run_script(
                    "generate_dataset_rules.py",
                    dataset_name
                )

            st.subheader(
                "Dataset Rule Output"
            )

            st.code(
                result.stdout
            )

            if result.stderr:
                st.subheader(
                    "Errors / Warnings"
                )

                st.error(
                    result.stderr
                )

            if result.returncode == 0:
                st.success(
                    "Dataset rule generation completed successfully."
                )
            else:
                st.error(
                    "Dataset rule generation failed."
                )

    st.divider()

    # =========================================================
    # FULL PIPELINE
    # =========================================================

    st.subheader("Run Full Pipeline")

    if st.button(
        "Run Full Pipeline",
        key="run_full_pipeline"
    ):

        if not steward_identity:
            st.error("Enter a steward identity before starting a full pipeline run.")
            result = None
        else:
            result = run_script(
                "run_pipeline.py",
                dataset_name,
                "--actor",
                steward_identity,
            )

        if result is not None:
            st.subheader(
                "Pipeline Output"
            )

            st.code(
                result.stdout
            )

            if result.stderr:

                st.subheader(
                    "Errors / Warnings"
                )

                st.error(
                    result.stderr
                )

            if result.returncode == 0:

                st.success(
                    "Pipeline completed successfully."
                )

            else:

                st.error(
                    "Pipeline failed."
                )

    # =========================================================
    # EVALUATE APPROVED RULES
    # =========================================================

    st.subheader(
        "Evaluate Rules Only"
    )

    if st.button(
        "Evaluate Approved Rules",
        key="evaluate_approved_rules"
    ):

        result = run_script(
            "evaluate_rules.py",
            dataset_name
        )

        st.subheader(
            "Rule Evaluation Output"
        )

        st.code(
            result.stdout
        )

        if result.stderr:

            st.error(
                result.stderr
            )

        if result.returncode == 0:

            st.success(
                "Approved rules evaluated successfully."
            )

        else:

            st.error(
                "Rule evaluation failed."
            )

    # =========================================================
    # REMEDIATION SUGGESTIONS
    # =========================================================

    st.subheader(
        "Generate Remediation Suggestions"
    )

    if st.button(
        "Generate Remediation Suggestions",
        key="generate_remediation_suggestions"
    ):

        result = run_script(
            "generate_remediation_suggestions.py",
            dataset_name
        )

        st.subheader(
            "Remediation Suggestion Output"
        )

        st.code(
            result.stdout
        )

        if result.stderr:

            st.error(
                result.stderr
            )

        if result.returncode == 0:

            st.success(
                "Remediation suggestions generated successfully."
            )

        else:

            st.error(
                "Generate remediation suggestions failed."
            )

    # =========================================================
    # APPLY APPROVED REMEDIATIONS
    # =========================================================

    st.subheader(
        "Apply Approved Remediations"
    )

    if st.button(
        "Apply Approved Remediations",
        key="apply_approved_remediations"
    ):
        if not steward_identity:
            st.error("Enter your steward identity before applying remediations.")
            result = None
        else:
            result = run_script(
                "apply_remediations.py",
                dataset_name,
                "--actor",
                steward_identity,
            )

        if result is None:
            st.stop()

        st.subheader(
            "Remediation Output"
        )

        st.code(
            result.stdout
        )

        if result.stderr:

            st.error(
                result.stderr
            )

        if result.returncode == 0:

            st.success(
                "Approved remediations applied successfully."
            )

        else:

            st.error(
                "Apply remediations failed."
            )
            
elif page == "Raw Data Preview":
    st.header("Raw Data Preview")

    datasets = get_datasets()
    dataset = st.selectbox(
        "Dataset",
        datasets
    )

    preview = read_sql(f"""
        SELECT *
        FROM raw.{dataset}
        LIMIT 100
    """)

    st.dataframe(preview, use_container_width=True)
