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


# Parent classes we want rules to apply to. The instance universe (used in
# applicability) is derived from these. Subclasses are auto-discovered from
# the IFC schema, unioned across IFC2X3 + IFC4.
TRACKED_PARENTS: list[str] = [
    "IfcWall", "IfcDoor", "IfcWindow", "IfcSlab", "IfcBeam", "IfcColumn",
    "IfcMember", "IfcPlate", "IfcStair", "IfcStairFlight", "IfcRamp",
    "IfcRampFlight", "IfcRoof", "IfcCovering", "IfcCurtainWall",
    "IfcRailing", "IfcFooting", "IfcPile", "IfcOpeningElement",
    "IfcBuildingElementProxy", "IfcChimney", "IfcShadingDevice",
]

_SCHEMAS = ("IFC2X3", "IFC4")


def _all_subtypes(decl) -> list[str]:
    """Recursive walk of an entity declaration's subtype tree (parent included)."""
    out = [decl.name()]
    for sub in decl.subtypes():
        out.extend(_all_subtypes(sub))
    return out


def subclasses_of(parent: str) -> list[str]:
    """Return parent + all schema-known subtypes, unioned across IFC2X3 and IFC4.

    Falls back to [parent] if the class doesn't exist in either schema.
    """
    import ifcopenshell
    found: set[str] = set()
    for v in _SCHEMAS:
        try:
            schema = ifcopenshell.schema_by_name(v)
            decl = schema.declaration_by_name(parent)
            found.update(_all_subtypes(decl))
        except Exception:
            continue
    return sorted(found) if found else [parent]


def _build_instance_universe() -> list[str]:
    """All concrete instance classes the rules apply to.

    Used to enumerate 'wrong classes' — for each rule, every class in this
    universe that is NOT the correct class generates one prohibited spec.
    """
    universe: set[str] = set()
    for p in TRACKED_PARENTS:
        universe.update(subclasses_of(p))
    return sorted(universe)


def expand_subclasses(correct_class: str) -> list[str]:
    """Return correct_class + its schema-known subtypes (treated as 'allowed')."""
    return subclasses_of(correct_class)


# Build once at import; cheap (just schema lookups).
INSTANCE_UNIVERSE: list[str] = _build_instance_universe()


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


def _entity_enum_xml(classes: list[str], indent: int) -> str:
    """(Kept for backward compat — unused after refactor)"""
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
    """Generate one prohibited-spec per wrong IFC class for this TypeID rule.

    Concept: 'BLK.* TypeID on IfcWindow → prohibited' is a single spec.
    For each rule we emit len(INSTANCE_UNIVERSE) - len(allowed) specs,
    one per class that should NOT carry this TypeID.
    """
    allowed = set(expand_subclasses(correct_class))
    wrong_classes = [c for c in INSTANCE_UNIVERSE if c not in allowed]
    note_part = f" — {note}" if note else ""
    instr_base = note or f"This TypeID should be on {correct_class} (or subclass), not on this class."

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
st.caption(
    "Genererar en IDS-fil som flaggar element där TypeID hamnat på fel IFC-klass. "
    "Varje regel → en spec per fel-klass (prohibited). Fungerar med ifctester och Solibri."
)

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
unknown_classes = [c for _, c, _ in rules if len(subclasses_of(c)) == 1 and subclasses_of(c)[0] == c]
if unknown_classes:
    st.warning(
        f"Klasser utan schema-kända subklasser (ingen expansion): "
        f"{', '.join(sorted(set(unknown_classes)))}. "
        f"Används som-de-är. Verifiera att namnet är korrekt."
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
