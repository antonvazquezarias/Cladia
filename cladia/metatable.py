"""Tables of sample data (CSV, TSV, Excel) for importing metadata: read into a header
and rows of text; the library matches the rows to sequences (library.import_table_metadata)."""
import csv
import datetime
import io

from .genbank import decode

MAX_ROWS = 50000
MAX_BYTES = 20 * 2**20
MAX_COLUMNS = 256

# What a column can be imported as: (key, label). The first five are the record's own header
# fields, the rest source qualifiers (what a GenBank submission asks for).
FIELDS = [
    ("description", "Description"), ("organism", "Organism"), ("accession", "Accession"),
    ("strain", "Strain"), ("isolate", "Isolate"), ("country", "Country"),
    ("collection_date", "Collection date"), ("host", "Host"), ("organelle", "Organelle"),
    ("specimen_voucher", "Specimen voucher"), ("isolation_source", "Isolation source"),
    ("lat_lon", "Latitude and longitude"), ("collected_by", "Collected by"),
    ("identified_by", "Identified by"), ("culture_collection", "Culture collection"),
    ("bio_material", "Biological material"),
]
SOURCE_KEYS = tuple(k for k, _ in FIELDS[3:]) + ("organism",)  # the qualifiers of the source feature


def read_table(filename, data):
    """(column names, rows) of a CSV / TSV / text / .xlsx file's first sheet; every cell a
    stripped string, short rows padded. The first row is the header (empty or repeated names
    are made up: "Column 3", "Host (2)")."""
    if len(data) > MAX_BYTES:
        raise ValueError("The metadata table exceeds 20 MB; split it into smaller tables")
    if data[:4] == b"\xd0\xcf\x11\xe0":
        raise ValueError("That is an old Excel file (.xls): save it as .xlsx or CSV first")
    if data[:2] == b"PK" or filename.lower().endswith((".xlsx", ".xlsm")):
        rows = _xlsx(data)
    else:
        rows = _delimited(decode(data))
    rows = [r for r in rows if any(c.strip() for c in r)]
    if len(rows) < 2:
        raise ValueError("The table needs a header row and at least one row of data")
    if len(rows) - 1 > MAX_ROWS:
        raise ValueError(f"The table exceeds {MAX_ROWS:,} rows; split it into smaller tables")
    width = max(len(r) for r in rows)
    if width > MAX_COLUMNS:
        raise ValueError(f"The table exceeds {MAX_COLUMNS} columns")
    pad = lambda r: [c.strip() for c in r] + [""] * (width - len(r))
    columns, seen = [], {}
    for i, name in enumerate(pad(rows[0])):
        name = name or f"Column {i + 1}"
        seen[name] = seen.get(name, 0) + 1
        columns.append(name if seen[name] == 1 else f"{name} ({seen[name]})")
    return columns, [pad(r) for r in rows[1:]]


def _delimited(text):
    """Rows of a delimited text: tab, semicolon (Spanish Excel's CSV) or comma, whichever the
    header line has most of."""
    first = next((line for line in text.splitlines() if line.strip()), "")
    delimiter = max("\t;,", key=lambda d: (first.count(d), d == "\t"))
    try:
        return _limited_rows(csv.reader(io.StringIO(text), delimiter=delimiter, strict=True))
    except csv.Error as exc:
        raise ValueError(f"Couldn't read the delimited table: {exc}") from exc


def _limited_rows(rows):
    result = []
    for row in rows:
        if not any(str(c).strip() for c in row):
            continue
        if len(row) > MAX_COLUMNS:
            raise ValueError(f"The table exceeds {MAX_COLUMNS} columns")
        result.append(row)
        if len(result) > MAX_ROWS + 1:
            raise ValueError(f"The table exceeds {MAX_ROWS:,} rows; split it into smaller tables")
    return result


def _xlsx(data):
    from openpyxl import load_workbook
    try:
        book = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as e:
        raise ValueError(f"Couldn't read that Excel file ({e})")
    try:
        sheet = book.worksheets[0]  # (the first sheet)
        if sheet.max_column and sheet.max_column > MAX_COLUMNS:
            raise ValueError(f"The table exceeds {MAX_COLUMNS} columns")
        return _limited_rows([_text(v) for v in row] for row in sheet.iter_rows(values_only=True))
    finally:
        book.close()


def _text(value):
    """A cell as text: dates as YYYY-MM-DD, whole numbers without .0."""
    if value is None:
        return ""
    if isinstance(value, datetime.datetime):
        return value.date().isoformat() if not (value.hour or value.minute or value.second) else value.isoformat(sep=" ")
    if isinstance(value, datetime.date):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)
