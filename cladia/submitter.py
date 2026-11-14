"""Submitters for GenBank submissions: who submits (contact, affiliation), the
authors and the reference, kept as named presets in the settings (one per
collaboration), and NCBI's submission template (.sbt, ASN.1 text) read from and
written for them. table2asn reads the template with -t."""
import re

from . import config

# A preset: all text. authors = one per line, "Last, First" (or "First Last").
FIELDS = {
    "name": "", "authors": "", "contact": "", "email": "", "phone": "",
    "institution": "", "department": "", "street": "", "city": "", "state": "",
    "postal_code": "", "country": "", "title": "", "citation": "unpublished",
    "hold": "",  # a release date (YYYY-MM-DD); empty = when processed
}


# ── The presets (settings.json) ──────────────────────────────────────────

def clean(preset):
    """A preset with only the known fields, all strings."""
    out = {k: str(preset.get(k) or "").strip() for k in FIELDS}
    out["citation"] = out["citation"] or FIELDS["citation"]
    return out


def presets():
    return [clean(p) for p in config.setting("submitters")]


def check_presets(items):
    """The list the settings dialog sends: cleaned, named, names unique."""
    out, seen = [], set()
    for item in items:
        preset = clean(item)
        if not preset["name"]:
            raise ValueError("Give every submitter a name")
        if preset["name"].lower() in seen:
            raise ValueError(f"Two submitters are called {preset['name']}")
        if preset["hold"] and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", preset["hold"]):
            raise ValueError(f"{preset['name']}: the release date is year-month-day")
        seen.add(preset["name"].lower())
        out.append(preset)
    return out


def find(name, overrides=None):
    """The preset called name with a submission's own authors/title/date on top."""
    found = next((p for p in presets() if p["name"] == name), None)
    if not found:
        raise ValueError(f"No submitter called {name}")
    changes = {k: v for k, v in (overrides or {}).items() if k in ("authors", "title", "hold", "citation")}
    return clean({**found, **changes})


def add_from_text(text, fallback_name):
    """An .sbt file kept as a new preset (named after its contact, else the file);
    returns the preset."""
    preset = read_sbt(text)
    base = preset["name"] = preset["name"] or fallback_name
    taken = {p["name"].lower() for p in presets()}
    n = 2
    while preset["name"].lower() in taken:
        preset["name"] = f"{base} {n}"
        n += 1
    config.save_settings({"submitters": presets() + [preset]})
    return preset


# ── Authors ──────────────────────────────────────────────────────────────

def parse_author(line):
    """{last, first, suffix} from "Last, First[, Jr.]" or "First [Middle] Last"."""
    parts = [p.strip() for p in line.split(",")]
    if len(parts) >= 2:
        return {"last": parts[0], "first": parts[1], "suffix": parts[2] if len(parts) > 2 else ""}
    words = line.split()
    return {"last": words[-1] if words else "", "first": " ".join(words[:-1]), "suffix": ""}


def authors_of(text):
    return [a for a in (parse_author(line) for line in text.splitlines() if line.strip()) if a["last"]]


def initials(first):
    """"Jean-Pierre R." -> "J.-P.R." (NCBI's initials of the given names)."""
    out = ""
    for word in first.split():
        out += "-".join(part[0].upper() + "." for part in word.split("-") if part and part[0].isalpha())
    return out


def author_line(last, first, suffix=""):
    return ", ".join(p for p in (last, first, suffix) if p)


# ── Writing the template ─────────────────────────────────────────────────

def _q(value):
    return '"' + str(value).replace('"', '""') + '"'


def _fields(pairs, indent):
    """"key value" lines, comma-separated, leaving out the empty ones."""
    pad = "  " * indent
    return (",\n").join(f"{pad}{key} {_q(value)}" for key, value in pairs if value)


def _name(author, indent):
    pad = "  " * indent
    body = _fields([("last", author["last"]), ("first", author["first"]), ("initials", initials(author["first"])),
                    ("suffix", author["suffix"])], indent + 1)
    return f"{pad}name name {{\n{body}\n{pad}}}"


def _affil(preset, indent, contact):
    pairs = [("affil", preset["institution"]), ("div", preset["department"]), ("city", preset["city"]),
             ("sub", preset["state"]), ("country", preset["country"]), ("street", preset["street"])]
    if contact:
        pairs += [("email", preset["email"]), ("phone", preset["phone"])]
    pairs.append(("postal-code", preset["postal_code"]))
    pad = "  " * indent
    return f"{pad}affil std {{\n{_fields(pairs, indent + 1)}\n{pad}}}"


def _names(authors, indent):
    pad = "  " * indent
    items = ",\n".join(f"{pad}  {{\n{_name(a, indent + 2)}\n{pad}  }}" for a in authors)
    return f"{pad}names std {{\n{items}\n{pad}}}"


def write_sbt(preset):
    """The submission template for a preset (authors needed; the contact is the first author unless named)."""
    authors = authors_of(preset["authors"])
    if not authors:
        raise ValueError("The submitter has no authors")
    contact = parse_author(preset["contact"]) if preset["contact"].strip() else authors[0]
    block = [
        "  contact {\n    contact {\n" + _name(contact, 3) + ",\n" + _affil(preset, 3, True) + "\n    }\n  }",
        "  cit {\n    authors {\n" + _names(authors, 3) + ",\n" + _affil(preset, 3, False) + "\n    }\n  }",
    ]
    if preset["hold"]:
        year, month, day = (int(x) for x in preset["hold"].split("-"))
        block.append("  hup TRUE")
        block.append(f"  reldate std {{\n    year {year},\n    month {month},\n    day {day}\n  }}")
    block.append("  subtype new")
    text = "Submit-block ::= {\n" + ",\n".join(block) + "\n}\n"
    gen = [f"      cit {_q(preset['citation'] or 'unpublished')}",
           "      authors {\n" + _names(authors, 4) + "\n      }"]
    if preset["title"]:
        gen.append(f"      title {_q(preset['title'])}")
    return text + "Seqdesc ::= pub {\n  pub {\n    gen {\n" + ",\n".join(gen) + "\n    }\n  }\n}\n"


# ── Reading a template ───────────────────────────────────────────────────
# ASN.1 text as a tree: a block is a list of (key, value) pairs (key None for
# the bare items of a list); values are strings, numbers, words or blocks.

TOKEN = re.compile(r'\s*(?:--[^\n]*|(?P<string>"(?:[^"]|"")*")|(?P<punct>::=|[{},])|(?P<word>[^\s{},"]+))')


def _tokens(text):
    pos = 0
    while pos < len(text):
        m = TOKEN.match(text, pos)
        if not m or m.end() == pos:
            break
        pos = m.end()
        if m.group("string") is not None:
            yield "string", m.group("string")[1:-1].replace('""', '"')
        elif m.group("punct"):
            yield "punct", m.group("punct")
        elif m.group("word"):
            yield "word", m.group("word")


def _parse(text):
    tokens = list(_tokens(text))
    at = 0

    def peek(k=0):
        return tokens[at + k] if at + k < len(tokens) else (None, None)

    def value():
        nonlocal at
        kind, tok = peek()
        if tok == "{":
            return block()
        at += 1
        if kind == "string":
            return tok
        if peek()[1] == "{":  # a choice written "name {...}" or "std {...}": the block
            return block()
        return int(tok) if re.fullmatch(r"-?\d+", tok or "") else tok

    def block():
        nonlocal at
        at += 1  # {
        items = []
        while peek()[1] not in ("}", None):
            if peek()[1] == "{":
                items.append((None, block()))
            else:
                key = peek()[1]
                at += 1
                items.append((key, value() if peek()[1] not in (",", "}") else True))
            if peek()[1] == ",":
                at += 1
        at += 1  # }
        return items

    out = {}
    while at < len(tokens):
        kind, name = peek()
        at += 1
        if peek()[1] == "::=":
            at += 1
            out[name] = value()
    return out


def _get(block, *path):
    """The value at a path of keys in a parsed block ("" when absent)."""
    for key in path:
        found = next((v for k, v in block if k == key), None) if isinstance(block, list) else None
        if found is None:
            return ""
        block = found
    return block


def _author_of(block):
    last, first = str(_get(block, "name", "last")), str(_get(block, "name", "first"))
    given = initials(first).replace("-", "").replace(".", "")
    extra = str(_get(block, "name", "initials")).replace("-", "").replace(".", "")[len(given):]
    if extra:  # initials beyond the first name's: middle names
        first = (first + " " + ".".join(extra) + ".").strip()
    return author_line(last, first, str(_get(block, "name", "suffix")))


def read_sbt(text):
    """A preset from a submission template (what it holds; the rest of the file is dropped)."""
    tree = _parse(text)
    submit = tree.get("Submit-block")
    if not isinstance(submit, list):
        raise ValueError("not a submission template (no Submit-block)")
    contact = _get(submit, "contact", "contact")
    authors_block = _get(submit, "cit", "authors")
    pub = _get(tree.get("Seqdesc", []), "pub", "gen")
    names = _get(authors_block, "names") or _get(pub, "authors", "names")
    authors = [_author_of(b) for k, b in names if k is None and isinstance(b, list)] if names else []
    affil = _get(authors_block, "affil") or _get(contact, "affil")
    contact_line = _author_of(contact) if contact else ""
    contact_affil = _get(contact, "affil")
    date = _get(submit, "reldate")
    hold = ""
    if date and _get(submit, "hup") == "TRUE":
        hold = "{:04d}-{:02d}-{:02d}".format(*(int(_get(date, k) or 1) for k in ("year", "month", "day")))
    first_line = authors[0] if authors else ""
    return clean({
        "name": contact_line.split(",")[0] if contact_line else "",
        "authors": "\n".join(authors),
        "contact": "" if contact_line == first_line else contact_line,
        "email": _get(contact_affil, "email"), "phone": _get(contact_affil, "phone"),
        "institution": _get(affil, "affil"), "department": _get(affil, "div"), "street": _get(affil, "street"),
        "city": _get(affil, "city"), "state": _get(affil, "sub"), "postal_code": _get(affil, "postal-code"),
        "country": _get(affil, "country"), "title": _get(pub, "title"),
        "citation": _get(pub, "cit") or "unpublished", "hold": hold,
    })
