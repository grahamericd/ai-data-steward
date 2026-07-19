import os
import pandas as pd
import streamlit as st
import sys
from pathlib import Path
from sqlalchemy import text
from sqlalchemy.engine import URL
import subprocess

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
    
from config import RAW_DATA_DIR, engine

st.set_page_config(page_title="Data Quality Lab", layout="wide")

st.title("AI-Assisted Data Quality Lab")

page = st.sidebar.radio(
    "Navigation",
    ["Dashboard", "Register Dataset", "Dataset 360", "Steward Workbench", "Rule Catalog", "Failed Records", "Remediation Queue", "Raw Data Preview", "Run Pipeline"]
)

def read_sql(query, params=None):
    return pd.read_sql(text(query), engine, params=params)
    
     
def get_datasets():
    df = read_sql("""
        SELECT  dataset_name
        FROM metadata.dataset
        WHERE active = TRUE
        ORDER BY display_name, dataset_name
    """)
    return df["dataset_name"].tolist()
    
def run_script(script_name, *args):
    project_dir = os.path.expanduser("~/projects/data-lab")
    python_path = os.path.join(project_dir, ".venv/bin/python")
    script_path = os.path.join(project_dir, "scripts", script_name)

    result = subprocess.run(
        [python_path, script_path, *args],
        cwd=project_dir,
        capture_output=True,
        text=True
    )

    return result
    

    
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
            created_at
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

        st.json(selected_rule["rule_definition"])

        col1, col2 = st.columns(2)

        with col1:
            if st.button("Approve Rule"):
                with engine.begin() as conn:
                    conn.execute(
                        text("""
                            UPDATE dq.rule
                            SET status = 'approved'
                            WHERE id = :id
                        """),
                        {"id": rule_id}
                    )
                st.success(f"Rule {rule_id} approved.")

        with col2:
            if st.button("Reject Rule"):
                with engine.begin() as conn:
                    conn.execute(
                        text("""
                            UPDATE dq.rule
                            SET status = 'rejected'
                            WHERE id = :id
                        """),
                        {"id": rule_id}
                    )
                st.warning(f"Rule {rule_id} rejected.")
                
                
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

        col1, col2, col3, col4 = st.columns(4)

        row_count = read_sql(f"""
            SELECT COUNT(*) AS row_count
            FROM {dataset_row["raw_schema"]}.{dataset_row["raw_table"]}
        """)

        column_count = read_sql("""
            SELECT COUNT(*) AS column_count
            FROM metadata.column_profile
            WHERE dataset_name = :dataset_name
        """, {"dataset_name": dataset_name})

        rule_count = read_sql("""
            SELECT COUNT(*) AS rule_count
            FROM dq.rule
            WHERE dataset_name = :dataset_name
        """, {"dataset_name": dataset_name})

        remediation_count = read_sql("""
            SELECT COUNT(*) AS remediation_count
            FROM dq.remediation_suggestion
            WHERE dataset_name = :dataset_name
        """, {"dataset_name": dataset_name})

        col1.metric("Rows", int(row_count.loc[0, "row_count"]))
        col2.metric("Columns", int(column_count.loc[0, "column_count"]))
        col3.metric("Rules", int(rule_count.loc[0, "rule_count"]))
        col4.metric("Remediations", int(remediation_count.loc[0, "remediation_count"]))

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

            c1, c2, c3, c4 = st.columns(4)

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
                column_name,
                rule_type,
                status,
                created_at,
                rule_definition
            FROM dq.rule
            WHERE dataset_name = :dataset_name
            ORDER BY created_at DESC
        """, {"dataset_name": dataset_name})

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
        st.markdown("### Raw vs Curated Preview")

        primary_key = dataset_row["primary_key"]

        if not primary_key:
            st.info("This dataset does not have a primary key configured.")
        else:
            curated_exists = read_sql("""
                SELECT EXISTS (
                    SELECT 1
                    FROM information_schema.tables
                    WHERE table_schema = 'curated'
                      AND table_name = :table_name
                ) AS exists
            """, {"table_name": dataset_row["raw_table"]})

            if not bool(curated_exists.loc[0, "exists"]):
                st.info("No curated table exists yet. Apply approved remediations first.")
            else:
                keys = read_sql(f"""
                    SELECT "{primary_key}" AS key_value
                    FROM {dataset_row["raw_schema"]}."{dataset_row["raw_table"]}"
                    LIMIT 100
                """)

                selected_key = st.selectbox(
                    "Select record",
                    keys["key_value"].tolist()
                )

                raw_record = read_sql(f"""
                    SELECT *
                    FROM {dataset_row["raw_schema"]}."{dataset_row["raw_table"]}"
                    WHERE "{primary_key}" = :key_value
                    LIMIT 1
                """, {"key_value": selected_key})

                curated_record = read_sql(f"""
                    SELECT *
                    FROM curated."{dataset_row["raw_table"]}"
                    WHERE "{primary_key}" = :key_value
                    LIMIT 1
                """, {"key_value": selected_key})

                col1, col2 = st.columns(2)

                with col1:
                    st.subheader("Raw")
                    st.dataframe(raw_record, use_container_width=True)

                with col2:
                    st.subheader("Curated")
                    st.dataframe(curated_record, use_container_width=True)

                st.markdown("### Applied Remediations")

                applied = read_sql("""
                    SELECT
                        id,
                        issue_type,
                        original_values,
                        suggested_values,
                        confidence_score,
                        approved_by,
                        approved_at
                    FROM dq.remediation_suggestion
                    WHERE dataset_name = :dataset_name
                      AND source_row_identifier = :key_value
                      AND status = 'applied'
                    ORDER BY approved_at DESC
                """, {
                    "dataset_name": dataset_name,
                    "key_value": selected_key
                })

                st.dataframe(applied, use_container_width=True)
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
            rule_definition
        FROM dq.rule
        ORDER BY created_at DESC
    """)

    st.dataframe(rules, use_container_width=True)

    st.subheader("Update Rule Status")

    rule_id = st.number_input("Rule ID", min_value=1, step=1)
    new_status = st.selectbox("New Status", ["proposed", "approved", "rejected", "retired"])

    if st.button("Update Rule"):
        with engine.begin() as conn:
            conn.execute(
                text("""
                    UPDATE dq.rule
                    SET status = :status
                    WHERE id = :id
                """),
                {"status": new_status, "id": rule_id}
            )
        st.success(f"Rule {rule_id} updated to {new_status}")

elif page == "Failed Records":
    st.header("Failed Records")

    failed_rules = read_sql("""
        SELECT
            r.id AS result_id,
            r.checked_at,
            r.dataset_name,
            ru.id AS rule_id,
            ru.column_name,
            ru.rule_type,
            r.result_status,
            r.failed_count,
            r.details
        FROM dq.result r
        LEFT JOIN dq.rule ru ON r.rule_id = ru.id
        WHERE r.result_status = 'FAIL'
        ORDER BY r.checked_at DESC
    """)

    if failed_rules.empty:
        st.success("No failed rule results found.")
    else:
        st.dataframe(failed_rules, use_container_width=True)

        result_id = st.selectbox(
            "Select failed result",
            failed_rules["result_id"].tolist()
        )

        selected = failed_rules[failed_rules["result_id"] == result_id].iloc[0]

        st.subheader("Failure Summary")
        st.write(f"Dataset: `{selected['dataset_name']}`")
        st.write(f"Column: `{selected['column_name']}`")
        st.write(f"Failed Records: `{selected['failed_count']}`")

        details = selected["details"]

        if isinstance(details, str):
            import json
            details = json.loads(details)

        sample_failures = details.get("sample_failures", [])

        st.subheader("Sample Failed Records")

        if sample_failures:
            st.dataframe(pd.DataFrame(sample_failures), use_container_width=True)
        else:
            st.info("No sample failures stored for this result.")


elif page == "Remediation Queue":

    st.header("Remediation Queue")

    status_filter = st.selectbox(
        "Status",
        ["proposed", "approved", "rejected", "applied", "all"]
    )

    if status_filter == "all":
        query = """
            SELECT *
            FROM dq.remediation_suggestion
            ORDER BY created_at DESC
            LIMIT 100
        """
        remediations = read_sql(query)

    else:
        query = """
            SELECT *
            FROM dq.remediation_suggestion
            WHERE status = :status
            ORDER BY created_at DESC
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

                with engine.begin() as conn:
                    conn.execute(
                        text("""
                            UPDATE dq.remediation_suggestion
                            SET status = 'approved',
                                approved_by = 'streamlit_user',
                                approved_at = now()
                            WHERE id = :id
                        """),
                        {"id": remediation_id}
                    )

                st.success(
                    f"Remediation {remediation_id} approved."
                )

        with col2:

            if st.button("Reject Suggestion"):

                with engine.begin() as conn:
                    conn.execute(
                        text("""
                            UPDATE dq.remediation_suggestion
                            SET status = 'rejected',
                                approved_by = 'streamlit_user',
                                approved_at = now()
                            WHERE id = :id
                        """),
                        {"id": remediation_id}
                    )

                st.warning(
                    f"Remediation {remediation_id} rejected."
                )

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
                        # approved_by = 'streamlit_user',
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

    st.write(f"Selected dataset: `{dataset_name}`")
    if st.button("Run Full Pipeline"):
        result = run_script(
            "run_pipeline.py",
            dataset_name
        )

        st.subheader("Pipeline Output")
        st.code(result.stdout)

        if result.stderr:
            st.subheader("Errors / Warnings")
            st.error(result.stderr)

        if result.returncode == 0:
            st.success("Pipeline completed successfully.")
        else:
            st.error("Pipeline failed.")
            
    st.subheader("Evaluate Rules Only")
    if st.button("Evaluate Approved Rules"):
        result = run_script(
            "evaluate_rules.py",
            dataset_name
        )

        st.subheader("Rule Evaluation Output")
        st.code(result.stdout)
        if result.stderr:
            st.error(result.stderr)
        if result.returncode == 0:
            st.success("Approved rules evaluated successfully.")
        else:
            st.error("Rule evaluation failed.")
    
    
    st.subheader("Generate Remediation Suggestions")
    if st.button("Generate Remediation Suggestions"):

        result = run_script(
            "generate_remediation_suggestions.py",
            dataset_name
        )
        st.subheader("Remediation Suggestion Output")
        st.code(result.stdout)
        if result.stderr:
            st.error(result.stderr)
        if result.returncode == 0:
            st.success("Remediation suggestions generated successfully.")
        else:
            st.error("Generate remediation suggestions failed.")
                
    st.subheader("Apply Approved Remediations")
    if st.button("Apply Approved Remediations"):
        result = run_script(
            "apply_remediations.py",
            dataset_name
        ) 
        st.subheader("Remediation Output")
        st.code(result.stdout)
        if result.stderr:
            st.error(result.stderr)
        if result.returncode == 0:
            st.success("Approved remediations applied successfully.")
        else:
            st.error("Apply remediations failed.")        
            
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
