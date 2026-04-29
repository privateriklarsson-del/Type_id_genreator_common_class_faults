# TypeID → IDS

Generate an [IDS](https://www.buildingsmart.org/standards/bsi-standards/information-delivery-specification-ids/) file that flags IFC elements where the `JM.TypeID` doesn't match the expected IFC class — e.g. balkongdörr exported as `IfcWindow`.

## How it works

Upload a 2-column CSV → get an `.ids` file back. Drop it into your IDS validator (Solibri, ifctester, BIMcollab Zoom).

### CSV format

```csv
TypeIDPattern,CorrectClass,Note
BLK.*,IfcDoor,Balkongdörr
BFx.*,IfcWindow,Blindfönster
```

- **TypeIDPattern**: XSD regex (full-match, auto-anchored). `BLK.*` = starts with BLK. No `^` or `$`.
- **CorrectClass**: e.g. `IfcDoor`, `IfcWindow`.
- **Note**: optional — shown in the IDS validation report.

## Run locally

```bash
pip install -r requirements.txt
streamlit run typeid_ids_app.py
```

## Deploy to Streamlit Cloud

1. Push this repo to GitHub
2. Go to [share.streamlit.io](https://share.streamlit.io)
3. New app → pick repo → main file: `typeid_ids_app.py` → deploy

## Notes

The check works whether `TypeID` is on the instance or inherited from the type object (`IfcDoorType`, `IfcWindowType`) — ifctester resolves type-inherited properties via `IfcRelDefinesByType`.
