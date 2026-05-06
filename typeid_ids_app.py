"""
Mini Streamlit: CSV → IDS for TypeID-vs-IFC-class checks.

CSV format:
    TypeIDPattern,CorrectClass,WrongClasses,Note
    BLK.*,IfcDoor,IfcWindow|IfcWindowStandardCase,Balkongdörr
    BFx.*,IfcWindow,IfcDoor|IfcDoorStandardCase|IfcWall,Blindfönster

Patterns use XSD regex (full-match, auto-anchored).
Use BLK.* for "starts with BLK". No ^ or $.

WrongClasses: pipe-separated list of IFC classes to check against.
List exactly the classes you want flagged — no subclass expansion.
"""
from __future__ import annotations

import csv
import io
from datetime import date
from xml.sax.saxutils import escape

import streamlit as st


SPEC_TEMPLATE = """    <specification name="{name}" ifcVersion="IFC2X3 IFC4">
      <applicability minOccurs="0" maxOccurs="unbounded">
        <entity><name><simpleValue>{wrong_class_upper}</simpleValue></name></entity>
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
        <property cardinality="prohibited" instructions="{instructions}">
          <propertySet><simpleValue>JM</simpleValue></propertySet>
          <baseName><simpleValue>TypeID</simpleValue></baseName>
        </property>
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


def _sniff_dialect(text: str) -> csv.Dialect:
    """Detect , vs ; separator (Swedish Excel uses ;)."""
    sample = text[:2048]
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        return csv.excel  # fall back to comma


def parse_rules(text: str) -> tuple[list[tuple[str, str, list[str], str]], list[str]]:
    """Return (rules, warnings). Rules = (pattern, correct_class, wrong_classes, note)."""
    rules, warnings = [], []
    dialect = _sniff_dialect(text)
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    if reader.fieldnames is None:
        return [], ["CSV is empty."]
    cols = {c.strip(): c for c in reader.fieldnames}

    required = ["TypeIDPattern", "CorrectClass", "WrongClasses"]
    missing = [c for c in required if c not in cols]
    if missing:
        return [], [f"Missing required columns: {missing}. Found: {list(cols)}."]

    for i, row in enumerate(reader, start=2):  # row 1 = header
        pattern = (row.get(cols["TypeIDPattern"]) or "").strip()
        correct = (row.get(cols["CorrectClass"]) or "").strip()
        wrong_raw = (row.get(cols["WrongClasses"]) or "").strip()
        note = (row.get(cols.get("Note", "Note")) or "").strip() if "Note" in cols else ""

        if not pattern or not correct:
            continue
        if not wrong_raw:
            warnings.append(f"Row {i}: skipped — WrongClasses is empty.")
            continue

        wrong_classes = [w.strip() for w in wrong_raw.split("|") if w.strip()]
        if not wrong_classes:
            warnings.append(f"Row {i}: skipped — WrongClasses parsed to empty list.")
            continue

        if pattern.startswith("^"):
            warnings.append(f"Row {i}: stripped leading '^' from '{pattern}' (XSD patterns are auto-anchored).")
            pattern = pattern[1:]
        if pattern.endswith("$"):
            warnings.append(f"Row {i}: stripped trailing '$' from '{pattern}'.")
            pattern = pattern[:-1]

        # Sanity: warn if the correct class ended up in wrong list (probably a mistake)
        overlap = [w for w in wrong_classes if w == correct]
        if overlap:
            warnings.append(
                f"Row {i}: '{correct}' is in both CorrectClass and WrongClasses — removed from wrong list."
            )
            wrong_classes = [w for w in wrong_classes if w != correct]
            if not wrong_classes:
                warnings.append(f"Row {i}: skipped — no wrong classes left after dedup.")
                continue

        rules.append((pattern, correct, wrong_classes, note))
    return rules, warnings


def build_spec(pattern: str, correct_class: str, wrong_classes: list[str], note: str) -> str:
    """Generate one prohibited-spec per user-specified wrong IFC class."""
    note_part = f" — {note}" if note else ""
    instr_base = note or f"This TypeID should be on {correct_class}, not on this class."

    specs = []
    for wrong in wrong_classes:
        name = f"TypeID '{pattern}' on {wrong} (should be {correct_class}){note_part}"
        instructions = f"{instr_base} Found on {wrong}."
        specs.append(SPEC_TEMPLATE.format(
            name=escape(name, {'"': "&quot;"}),
            pattern=escape(pattern, {'"': "&quot;"}),
            wrong_class_upper=wrong.upper(),
            instructions=escape(instructions, {'"': "&quot;"}),
        ))
    return "\n".join(specs)


def build_ids(rules: list[tuple[str, str, list[str], str]]) -> str:
    specs = "\n".join(build_spec(p, c, w, n) for p, c, w, n in rules)
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
st.caption(
    "Genererar en IDS-fil som flaggar element där TypeID hamnat på fel IFC-klass. "
    "Varje regel → en spec per fel-klass (prohibited). Fungerar med ifctester och Solibri."
)

with st.expander("📋 CSV-format"):
    st.code(
        "TypeIDPattern,CorrectClass,WrongClasses,Note\n"
        "BLK.*,IfcDoor,IfcWindow|IfcWindowStandardCase,Balkongdörr\n"
        "BFx.*,IfcWindow,IfcDoor|IfcDoorStandardCase|IfcWall,Blindfönster\n",
        language="csv",
    )
    st.markdown(
        "- **TypeIDPattern**: XSD regex. `BLK.*` = börjar med BLK. Inga `^` eller `$`.\n"
        "- **CorrectClass**: t.ex. `IfcDoor` (vad det _ska_ vara).\n"
        "- **WrongClasses**: pipe-separerad lista av klasser att flagga, t.ex. "
        "`IfcWindow|IfcWindowStandardCase`. Inga subklasser auto-expanderas — "
        "lista exakt vad du vill kontrollera.\n"
        "- **Note**: valfri kommentar (visas i IDS-rapporten)."
    )

uploaded = st.file_uploader("Ladda upp regel-CSV", type=["csv"])

# Sample download
st.download_button(
    "⬇️ Ladda ner exempel-CSV",
    data=(
        "TypeIDPattern,CorrectClass,WrongClasses,Note\n"
        "BLK.*,IfcDoor,IfcWindow|IfcWindowStandardCase,Balkongdörr\n"
        "BFx.*,IfcWindow,IfcDoor|IfcDoorStandardCase|IfcWall,Blindfönster\n"
    ),
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
    [
        {
            "Pattern": p,
            "Correct class": c,
            "Wrong classes (flagged)": ", ".join(w),
            "Note": n,
        }
        for p, c, w, n in rules
    ],
    use_container_width=True, hide_index=True,
)

ids_text = build_ids(rules)

err = validate_with_ifctester(ids_text)
if err:
    st.error(f"IDS-validering misslyckades: {err}")
    with st.expander("Visa genererad XML"):
        st.code(ids_text, language="xml")
    st.stop()

st.success(f"✅ IDS validerad av ifctester ({len(rules)} regler → {ids_text.count('<specification ')} specs).")

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
