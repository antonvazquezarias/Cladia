"""Searching NCBI GenBank (nucleotide) with the E-utilities: the Download dialog's
Search. The query is NCBI's own: plain words, or fields such as
"Fucus[organism] AND complete genome". Downloading is tool_plugins.genbank_download.run_download."""

import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import config

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
PAGE = 100  # results per page
GAP = 0.4   # s between requests: NCBI allows 3 a second without an API key
KEY_GAP = 0.11  # and 10 with one
_lock, _last = threading.Lock(), [0.0]


def identity():
    """What NCBI asks programs to send: the tool's name, an e-mail to contact,
    and the API key if there is one (the Settings dialog)."""
    params = {"tool": "Cladia"}
    if config.setting("ncbi_email"):
        params["email"] = config.setting("ncbi_email")
    if config.setting("ncbi_api_key"):
        params["api_key"] = config.setting("ncbi_api_key")
    return params


def pause():
    """Seconds between E-utilities requests: NCBI's limit, faster with a key."""
    return KEY_GAP if config.setting("ncbi_api_key") else GAP


RETRY_WAITS = (1, 2, 3, 5, 8)  # s before each new try after "too many requests" or a server error


def _message(code, body):
    """NCBI's error as one readable line: server errors come as an HTML page."""
    if code == 429:
        return "NCBI says too many requests. Wait a minute and try again (an API key in Settings raises the limit)."
    if code >= 500:
        return f"NCBI's server had an error ({code}) and kept failing after {len(RETRY_WAITS)} retries. Try again in a few minutes."
    text = re.sub(r"<[^>]+>", " ", body) if "<html" in body.lower() else body
    return f"NCBI said {code}: {' '.join(text.split())[:300]}"


def post(url, params, timeout=60):
    """One request to NCBI (E-utilities spaced to its limit), its text; retried after
    "too many requests" (429) and server errors (5xx): NCBI has spells where most
    requests fail at random (Nov 2026: 4 in 5) and a retry often gets through."""
    data = urllib.parse.urlencode(params).encode()
    waits = RETRY_WAITS
    for attempt in range(len(waits) + 1):
        with _lock:  # one request at a time, spaced
            time.sleep(max(0.0, _last[0] + pause() - time.time()))
            _last[0] = time.time()
        try:
            with urllib.request.urlopen(url, data=data, timeout=timeout) as response:
                return response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if (e.code == 429 or e.code >= 500) and attempt < len(waits):
                time.sleep(waits[attempt])
                continue
            raise ValueError(_message(e.code, e.read().decode("utf-8", "replace") or e.reason)) from None
        except OSError as e:
            raise ValueError(f"Couldn't reach NCBI ({e}). Is this PC online?") from None


def _get(tool, text=False, **params):
    """One E-utilities request: its JSON, or its text (efetch)."""
    body = post(f"{EUTILS}/{tool}.fcgi", {"db": "nuccore", "retmode": "text" if text else "json",
                                          **identity(), **params}, timeout=300 if text else 60)
    return body if text else json.loads(body)


def search(query, start=0):
    """{count, start, results: [{accession, title, organism, length, date}]}, PAGE at
    a time, most relevant first as on NCBI's website (E-utilities' own order is the
    newest first: "fucus complete genome" gave this year's bacteria isolated from
    Fucus before the Fucus genomes)."""
    found = _get("esearch", term=query, retstart=start, retmax=PAGE, sort="relevance")["esearchresult"]
    if "ERROR" in found:
        raise ValueError(f"NCBI: {found['ERROR']}")
    ids = found.get("idlist", [])
    results = []
    if ids:
        summary = _get("esummary", id=",".join(ids))["result"]
        for uid in summary.get("uids", []):
            s = summary[uid]
            results.append({"accession": s.get("accessionversion") or s.get("caption"), "title": s.get("title", ""),
                            "organism": s.get("organism", ""), "length": s.get("slen", 0),
                            "date": s.get("updatedate") or s.get("createdate", "")})
    return {"count": int(found.get("count", 0)), "start": start, "results": results}


# ── Whole searches (BLAST databases from GenBank) ────────────────────────
# NCBI's history server keeps a search's results (WebEnv + query_key), so they
# can be fetched in batches without sending thousands of ids.

SAMPLE = 500  # records whose lengths and titles stand for the whole search


def history(query):
    """(count, webenv, query_key) of a search kept on NCBI's history server."""
    found = _get("esearch", term=query, usehistory="y", retmax=0)["esearchresult"]
    if "ERROR" in found:
        raise ValueError(f"NCBI: {found['ERROR']}")
    return int(found.get("count", 0)), found.get("webenv"), found.get("querykey")


def sample(webenv, key, count):
    """Mean length and title length of up to SAMPLE records spread over the search."""
    step = max(1, count // SAMPLE)
    lengths, titles = [], []
    for start in range(0, count, step * 100) if step > 1 else [0]:  # pieces of 100 from across the search
        summary = _get("esummary", WebEnv=webenv, query_key=key, retstart=start,
                       retmax=min(SAMPLE, 100 if step > 1 else SAMPLE))["result"]
        for uid in summary.get("uids", []):
            lengths.append(summary[uid].get("slen", 0))
            titles.append(len(summary[uid].get("title", "")) + len(summary[uid].get("accessionversion", "")))
        if len(lengths) >= SAMPLE:
            break
    return sum(lengths) / max(1, len(lengths)), sum(titles) / max(1, len(titles))


def fetch_fasta(webenv, key, start, n):
    """Records start..start+n of a kept search, as FASTA text."""
    return _get("efetch", text=True, WebEnv=webenv, query_key=key, retstart=start, retmax=n, rettype="fasta")
