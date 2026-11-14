"""Manual/native UI probe of the real WindowApi ZIP dialogs, using scratch data."""
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import webview
from Bio.Seq import Seq
from Bio.SeqRecord import SeqRecord
from cladia import config, library, projects
from cladia.window import WindowApi


def main():
    with tempfile.TemporaryDirectory(prefix="cladia-native-probe-") as temp:
        root = Path(temp)
        config.DATA_DIR, config.APP_DIR = root / "data", root / "data" / ".cladia"
        library.ensure_folders()
        library.add_sequence(SeqRecord(Seq("ACGT"), id="probe", name="probe", annotations={"molecule_type": "DNA"}), "probe")
        events = []
        class Probe(WindowApi):
            def export_probe(self):
                path = self.save_file(str(root / "native-export.zip"), "zip")
                result = {"action": "export", "cancelled": not path}
                if path:
                    result.update(projects.export_zip(path), path=path)
                events.append(result)
                return result

            def import_probe(self):
                path = self.pick_project()
                result = {"action": "import", "cancelled": not path}
                if path:
                    with tempfile.TemporaryDirectory(dir=root) as staging:
                        manifest = projects.validate_zip(path, Path(staging))
                    result.update(items=len(manifest["entries"]), path=path)
                events.append(result)
                return result

        html = """<html><body style="font:18px Segoe UI;padding:25px">
          <h2>Cladia ZIP dialog diagnostics</h2><p>Scratch data only.</p>
          <button onclick="pywebview.api.export_probe().then(x=>result.textContent=JSON.stringify(x))">Export ZIP</button>
          <button onclick="pywebview.api.import_probe().then(x=>result.textContent=JSON.stringify(x))">Import ZIP</button>
          <pre id="result" style="white-space:pre-wrap"></pre></body></html>"""
        webview.create_window("Cladia ZIP dialog diagnostics", html=html, js_api=Probe(""), width=900, height=400)
        webview.start()
        print(json.dumps(events, indent=2))


if __name__ == "__main__":
    main()
