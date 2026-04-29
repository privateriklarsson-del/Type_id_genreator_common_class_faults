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


# IFC class → list of valid instance subclasses.
# Auto-expand so e.g. IfcWindow rules also pass IfcWindowStandardCase.
SUBCLASS_MAP: dict[str, list[str]] = {
    "IfcWindow":    ["IfcWindow", "IfcWindowStandardCase"],
    "IfcDoor":      ["IfcDoor", "IfcDoorStandardCase"],
    "IfcWall":      ["IfcWall", "IfcWallStandardCase", "IfcWallElementedCase"],
    "IfcSlab":      ["IfcSlab", "IfcSlabStandardCase", "IfcSlabElementedCase"],
    "IfcBeam":      ["IfcBeam", "IfcBeamStandardCase"],
    "IfcColumn":    ["IfcColumn", "IfcColumnStandardCase"],
    "IfcMember":    ["IfcMember", "IfcMemberStandardCase"],
    "IfcPlate":     ["IfcPlate", "IfcPlateStandardCase"],
    "IfcStair":     ["IfcStair"],
    "IfcStairFlight": ["IfcStairFlight"],
    "IfcRamp":      ["IfcRamp"],
    "IfcRampFlight": ["IfcRampFlight"],
    "IfcRoof":      ["IfcRoof"],
    "IfcCovering":  ["IfcCovering"],
    "IfcCurtainWall": ["IfcCurtainWall"],
    "IfcRailing":   ["IfcRailing"],
    "IfcFooting":   ["IfcFooting"],
    "IfcPile":      ["IfcPile"],
    "IfcOpeningElement": ["IfcOpeningElement"],
    "IfcBuildingElementProxy": ["IfcBuildingElementProxy"],
    "IfcChimney":   ["IfcChimney"],
    "IfcShadingDevice": ["IfcShadingDevice"],
}

# Universe of instance product classes that the rules apply to.
# Type objects (IfcWindowType, IfcDoorStyle, etc.) are NOT in this list,
# so they're excluded from applicability automatically.
INSTANCE_UNIVERSE: list[str] = sorted({c for sub in SUBCLASS_MAP.values() for c in sub})


def expand_subclasses(correct_class: str) -> list[str]:
    """Return correct_class plus its known instance subclasses (auto-expanded)."""
    return SUBCLASS_MAP.get(correct_class, [correct_class])


def _entity_enum_xml(classes: list[str], indent: int) -> str:
    """Build an entity facet's <name> as an enumeration of classes (uppercased)."""
    pad = " " * indent
    enums = "\n".join(
        f'{pad}              <xs:enumeration value="{escape(c.upper())}"/>'
        for c in classes
    )
    return (
        f"{pad}<name>\n"
        f"{pad}            <xs:restriction base=\"xs:string\">\n"
        f"{enums}\n"
        f"{pad}            </xs:restriction>\n"
        f"{pad}          </name>"
    )


SPEC_TEMPLATE = """    <specification name="{name}" ifcVersion="IFC2X3 IFC4">
      <applicability minOccurs="0" maxOccurs="unbounded">
        <entity>
          {applicability_name}
        </entity>
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
          {requirement_name}
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


def _sniff_dialect(text: str) -> csv.Dialect:
    """Detect , vs ; separator (Swedish Excel uses ;)."""
    sample = text[:2048]
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        return csv.excel  # fall back to comma


def parse_rules(text: str) -> tuple[list[tuple[str, str, str]], list[str]]:
    """Return (rules, warnings). Rules = (pattern, class, note)."""
    rules, warnings = [], []
    dialect = _sniff_dialect(text)
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
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
    valid_classes = expand_subclasses(correct_class)
    name = f"TypeID '{pattern}' must be {correct_class}"
    if len(valid_classes) > 1:
        name += f" (or subclass)"
    if note:
        name += f" — {note}"
    instructions = note or f"Element should be exported as {correct_class} (or a valid subclass)."
    return SPEC_TEMPLATE.format(
        name=escape(name, {'"': "&quot;"}),
        pattern=escape(pattern, {'"': "&quot;"}),
        applicability_name=_entity_enum_xml(INSTANCE_UNIVERSE, indent=8),
        requirement_name=_entity_enum_xml(valid_classes, indent=8),
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
unknown_classes = [c for _, c, _ in rules if c not in SUBCLASS_MAP]
if unknown_classes:
    st.warning(
        f"Okända klasser (ingen subklass-expansion sker): {', '.join(sorted(set(unknown_classes)))}. "
        f"De används som-de-är. Lägg till i SUBCLASS_MAP om du vill auto-expandera."
    )

st.dataframe(
    [
        {
            "Pattern": p,
            "Correct class": c,
            "Expanded valid classes": ", ".join(expand_subclasses(c)),
            "Note": n,
        }
        for p, c, n in rules
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
