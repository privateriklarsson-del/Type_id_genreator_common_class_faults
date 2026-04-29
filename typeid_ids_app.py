"""
Mini Streamlit: CSV → IDS for TypeID-vs-IFC-class checks.

CSV format:
    TypeIDPattern,CorrectClass,Note
    BLK.*,IfcDoor,Balkongdörr
    BFx.*,IfcWindow,Blindfönster

Patterns use XSD regex (full-match, auto-anchored).
Use BLK.* for "starts with BLK". No ^ or $.
"""
from __future__ import annotations

import csv
import io
from datetime import date
from xml.sax.saxutils import escape

import streamlit as st


SPEC_TEMPLATE = """    <specification name="{name}" ifcVersion="IFC2X3 IFC4">
      <applicability minOccurs="0" maxOccurs="unbounded">
        <property>
          <propertySet><simpleValue>JM</simpleValue></propertySet>
          <baseName><simpleValue>TypeID</simpleValue></baseName>
          <value>
            <xs:restriction base="xs:string">
              <xs:pattern value="{pattern}"/>
            </xs:restriction>
          </value>
        </property>
      </applicability>
      <requirements>
        <entity instructions="{instructions}">
          <name><simpleValue>{ifc_class_upper}</simpleValue></name>
        </entity>
      </requirements>
    </specification>"""

IDS_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<ids xmlns="http://standards.buildingsmart.org/IDS"
     xmlns:xs="http://www.w3.org/2001/XMLSchema"
     xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
     xsi:schemaLocation="http://standards.buildingsmart.org/IDS http://standards.buildingsmart.org/IDS/1.0/ids.xsd">
  <info>
    <title>TypeID vs IFC class consistency</title>
    <description>Flags elements whose JM.TypeID matches a known pattern but whose IFC class is wrong.</description>
    <author>bim@jm.se</author>
    <date>{today}</date>
  </info>
  <specifications>
{specs}
  </specifications>
</ids>
"""


def parse_rules(text: str) -> tuple[list[tuple[str, str, str]], list[str]]:
    """Return (rules, warnings). Rules = (pattern, class, note)."""
    rules, warnings = [], []
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        return [], ["CSV is empty."]
    cols = {c.strip(): c for c in reader.fieldnames}
    if "TypeIDPattern" not in cols or "CorrectClass" not in cols:
        return [], [f"Missing required columns. Found: {list(cols)}. Need: TypeIDPattern, CorrectClass."]

    for i, row in enumerate(reader, start=2):  # row 1 = header
        pattern = (row.get(cols["TypeIDPattern"]) or "").strip()
        correct = (row.get(cols.get("CorrectClass", "")) or "").strip()
        note = (row.get(cols.get("Note", "Note")) or "").strip() if "Note" in cols else ""
        if not pattern or not correct:
            continue
        if pattern.startswith("^"):
            warnings.append(f"Row {i}: stripped leading '^' from '{pattern}' (XSD patterns are auto-anchored).")
            pattern = pattern[1:]
        if pattern.endswith("$"):
            warnings.append(f"Row {i}: stripped trailing '$' from '{pattern}'.")
            pattern = pattern[:-1]
        rules.append((pattern, correct, note))
    return rules, warnings


def build_spec(pattern: str, correct_class: str, note: str) -> str:
    cls_upper = correct_class.upper()
    if not cls_upper.startswith("IFC"):
        cls_upper = "IFC" + cls_upper
    name = f"TypeID '{pattern}' must be {correct_class}"
    if note:
        name += f" ({note})"
    instructions = note or f"Element should be exported as {correct_class}, not the current class."
    return SPEC_TEMPLATE.format(
        name=escape(name, {'"': "&quot;"}),
        pattern=escape(pattern, {'"': "&quot;"}),
        ifc_class_upper=cls_upper,
        instructions=escape(instructions, {'"': "&quot;"}),
    )


def build_ids(rules: list[tuple[str, str, str]]) -> str:
    specs = "\n".join(build_spec(p, c, n) for p, c, n in rules)
    return IDS_TEMPLATE.format(today=date.today().isoformat(), specs=specs)


def validate_with_ifctester(ids_text: str) -> str | None:
    """Try to load with ifctester. Return error message or None."""
    try:
        import tempfile, ifctester.ids
        with tempfile.NamedTemporaryFile(suffix=".ids", delete=False, mode="w", encoding="utf-8") as f:
            f.write(ids_text)
            path = f.name
        ifctester.ids.open(path)
        return None
    except Exception as e:
        return str(e)


# --- UI ---
st.set_page_config(page_title="TypeID → IDS", page_icon="🛠️", layout="centered")
st.title("🛠️ TypeID-klass kontroll → IDS")
st.caption("Generera en IDS-fil som flaggar element där TypeID inte matchar förväntad IFC-klass.")

with st.expander("📋 CSV-format"):
    st.code(
        "TypeIDPattern,CorrectClass,Note\n"
        "BLK.*,IfcDoor,Balkongdörr\n"
        "BFx.*,IfcWindow,Blindfönster\n",
        language="csv",
    )
    st.markdown(
        "- **TypeIDPattern**: XSD regex. `BLK.*` = börjar med BLK. Inga `^` eller `$`.\n"
        "- **CorrectClass**: t.ex. `IfcDoor`, `IfcWindow`.\n"
        "- **Note**: valfri kommentar (visas i IDS-rapporten)."
    )

uploaded = st.file_uploader("Ladda upp regel-CSV", type=["csv"])

# Sample download
st.download_button(
    "⬇️ Ladda ner exempel-CSV",
    data="TypeIDPattern,CorrectClass,Note\nBLK.*,IfcDoor,Balkongdörr\nBFx.*,IfcWindow,Blindfönster\n",
    file_name="typeid_class_rules_example.csv",
    mime="text/csv",
)

if uploaded is None:
    st.info("Ladda upp en CSV för att fortsätta.")
    st.stop()

text = uploaded.getvalue().decode("utf-8-sig")
rules, warnings = parse_rules(text)

if not rules:
    st.error("Inga giltiga regler hittades.")
    for w in warnings:
        st.warning(w)
    st.stop()

for w in warnings:
    st.warning(w)

st.subheader(f"Regler ({len(rules)})")
st.dataframe(
    [{"Pattern": p, "Correct class": c, "Note": n} for p, c, n in rules],
    use_container_width=True, hide_index=True,
)

ids_text = build_ids(rules)

err = validate_with_ifctester(ids_text)
if err:
    st.error(f"IDS-validering misslyckades: {err}")
    with st.expander("Visa genererad XML"):
        st.code(ids_text, language="xml")
    st.stop()

st.success("✅ IDS validerad av ifctester.")

filename = f"typeid_class_{date.today().strftime('%Y%m%d')}.ids"
st.download_button(
    "⬇️ Ladda ner IDS",
    data=ids_text,
    file_name=filename,
    mime="application/xml",
    type="primary",
)

with st.expander("Förhandsgranska XML"):
    st.code(ids_text, language="xml")
