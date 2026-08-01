
    st.error(f"Could not read metadata.dataset: {exc}")
    st.stop()

if not datasets:
    st.warning("No active datasets were found.")
    st.stop()

dataset_by_name = {
    dataset["dataset_name"]: dataset
    for dataset in datasets
}

selected_name = st.selectbox(
    "Dataset",
    options=list(dataset_by_name),
    format_func=lambda name: (
        dataset_by_name[name].get("display_name")
        or name
    ),
)

dataset = dataset_by_name[selected_name]

details = st.columns(4)
details[0].metric(
    "Source type",
    dataset.get("source_type") or "Not set",
)
details[1].metric(
    "Target table",
    f"{dataset.get('raw_schema')}.{dataset.get('raw_table')}",
)
details[2].metric(
    "Primary key",
    dataset.get("primary_key") or "Not set",
)
details[3].metric(
    "Default mode",
    dataset.get("load_mode") or "upsert",
)

if dataset.get("description"):
    st.info(dataset["description"])

uploaded_file = st.file_uploader(
    "Choose an incremental source file",
)

registered_mode = (
    dataset.get("load_mode")
    or "upsert"
).lower()

mode_options = ["upsert", "append", "replace"]

default_index = (
    mode_options.index(registered_mode)
    if registered_mode in mode_options
    else 0
)

load_mode = st.selectbox(
    "Load mode",
    options=mode_options,
    index=default_index,
)

if load_mode == "replace":
    st.warning(
        "Replace mode deletes and rebuilds the existing raw table."
    )

if load_mode == "upsert" and not dataset.get("primary_key"):
    st.error(
        "This dataset has no registered primary key. "
        "Upsert mode cannot run."
    )

temporary_path = None

if uploaded_file is not None:
    suffix = Path(uploaded_file.name).suffix

    with tempfile.NamedTemporaryFile(
        delete=False,
        suffix=suffix,
    ) as temporary_file:
        temporary_file.write(uploaded_file.getbuffer())
        temporary_path = Path(temporary_file.name)

    st.success(f"Selected file: {uploaded_file.name}")

    with st.expander(
        "Preview first 20 parsed rows",
        expanded=True,
    ):
        try:
            preview = create_preview(
                dataset,
                temporary_path,
            )

            st.dataframe(
                preview,
                use_container_width=True,
                hide_index=True,
            )

            st.caption(
                f"{len(preview)} preview rows · "
                f"{len(preview.columns)} columns"
            )

        except Exception as exc:
            st.error(f"Could not preview the file: {exc}")

load_disabled = (
    uploaded_file is None
    or (
        load_mode == "upsert"
        and not dataset.get("primary_key")
    )
)

if st.button(
    "Load File",
    type="primary",
    disabled=load_disabled,
    use_container_width=True,
):
    try:
        with st.spinner("Parsing and loading the file..."):
            result = load_dataset(
                dataset_name=selected_name,
                supplied_file=str(temporary_path),
                requested_mode=load_mode,
            )

        st.success("The dataset loaded successfully.")

        metrics = st.columns(3)
        metrics[0].metric(
            "Rows received",
            result.get("rows_received", 0),
        )
        metrics[1].metric(
            "Rows inserted",
            result.get("rows_inserted", 0),
        )
        metrics[2].metric(
            "Rows updated",
            result.get("rows_updated", 0),
        )

        with st.expander("Load details"):
            st.json(result)

        st.cache_data.clear()

    except Exception as exc:
        st.error(f"Load failed: {exc}")

    finally:
        if temporary_path and temporary_path.exists():
            temporary_path.unlink(missing_ok=True)
