import streamlit as st
from converter_v1_ui import convert_hp_to_mpf_text, normalize_power_head

st.set_page_config(page_title="HP to MPF Converter", layout="wide")

st.title("TRUMPF HP to BEaM MPF Converter")

# -----------------------------
# Sidebar conversion settings
# -----------------------------
st.sidebar.header("Conversion Settings")

power_head_label = st.sidebar.radio(
    "Select laser power head",
    ["10Vx", "24Vx"],
    index=1,
    help="This controls the PUIS_SET formula and gas settings used in the MPF output."
)

power_head = normalize_power_head(power_head_label)

st.sidebar.markdown(
    """
**Power formula used**

- **10Vx**: `(POWER + 194.55) / 22.487`
- **24Vx**: `(POWER + 165.73) / 21.832`
"""
)

# -----------------------------
# File upload
# -----------------------------
uploaded_file = st.file_uploader(
    "Upload original .HP file",
    type=["hp", "txt"]
)

# -----------------------------
# Session state
# -----------------------------
if "hp_text" not in st.session_state:
    st.session_state.hp_text = ""

if "source_name" not in st.session_state:
    st.session_state.source_name = "uploaded.HP"

if "last_uploaded_name" not in st.session_state:
    st.session_state.last_uploaded_name = None

if "mpf_text" not in st.session_state:
    st.session_state.mpf_text = ""

if "last_power_head" not in st.session_state:
    st.session_state.last_power_head = power_head

# Load uploaded file only when a new file is uploaded.
# This prevents Streamlit reruns from wiping manual edits.
if uploaded_file is not None:
    uploaded_text = uploaded_file.read().decode("utf-8", errors="replace")

    if uploaded_file.name != st.session_state.last_uploaded_name:
        st.session_state.hp_text = uploaded_text
        st.session_state.source_name = uploaded_file.name
        st.session_state.last_uploaded_name = uploaded_file.name
        st.session_state.mpf_text = ""

# Clear old output if power head changes, so user reconverts intentionally.
if power_head != st.session_state.last_power_head:
    st.session_state.last_power_head = power_head
    st.session_state.mpf_text = ""

# -----------------------------
# Main UI
# -----------------------------
if st.session_state.hp_text:
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Editable HP Input")
        st.caption(
            "Edit the uploaded HP code here before conversion. "
            "For example, you can change RAPIDFEED, WELDFEED, POWER, WIDTH, OVERLAP, etc."
        )

        edited_hp_text = st.text_area(
            "Edit HP code before conversion",
            value=st.session_state.hp_text,
            height=650,
            key="hp_editor"
        )

        st.session_state.hp_text = edited_hp_text

    with col2:
        st.subheader("Converted MPF Output")
        st.caption(f"Selected power head: **{power_head_label}**")

        if st.button("Convert edited HP to MPF", type="primary"):
            try:
                st.session_state.mpf_text = convert_hp_to_mpf_text(
                    st.session_state.hp_text,
                    source_name=st.session_state.source_name,
                    power_head=power_head
                )
                st.success("Conversion completed.")
            except Exception as e:
                st.session_state.mpf_text = ""
                st.error(f"Conversion failed: {e}")

        if st.session_state.mpf_text:
            st.text_area(
                "Generated MPF",
                value=st.session_state.mpf_text,
                height=650
            )

            output_name = (
                st.session_state.source_name.rsplit(".", 1)[0]
                + f"_{power_head}_converted.MPF"
            )

            st.download_button(
                label="Download MPF file",
                data=st.session_state.mpf_text,
                file_name=output_name,
                mime="text/plain"
            )
else:
    st.info("Upload a .HP file to begin.")
