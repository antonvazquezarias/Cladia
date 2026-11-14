"""Exporting figures (the figure editor, app.js): SVG, PDF, PNG and TIFF.

The page draws a figure as SVG (sizes in millimetres, units in points), and,
for bitmaps, as PNG at the resolution asked. PDF is made from the SVG with
svglib + reportlab, so it stays vector; fonts become PDF's standard ones
(Arial and Calibri as Helvetica, Times New Roman as Times, with their bold and italic), which need no
embedding. PNG and TIFF are re-encoded with Pillow, with their dpi (TIFF LZW-compressed).
"""

import base64
import re
from io import BytesIO

FORMATS = ("svg", "pdf", "png", "tiff")
# font-family in the SVG -> PDF's standard fonts (the same look, no fonts to embed)
# (families svglib combines with bold and italic: "Times", not "Times-Roman")
PDF_FONTS = {"Arial": "Helvetica", "Helvetica": "Helvetica", "Calibri": "Helvetica",
             "Times New Roman": "Times", "Times": "Times", "Georgia": "Times", "Courier New": "Courier"}


def export(fmt, svg="", png="", dpi=300):
    if fmt not in FORMATS:
        raise ValueError(f"Unknown format: {fmt}")
    if fmt == "svg":
        return svg.encode("utf-8")
    if fmt == "pdf":
        return pdf(svg)
    from PIL import Image
    image = Image.open(BytesIO(base64.b64decode(png))).convert("RGB")  # no transparency: journals ask that
    out = BytesIO()
    if fmt == "png":  # the page's PNG has no resolution in it: programs would open it at 96 dpi
        image.save(out, "PNG", dpi=(dpi, dpi), optimize=True)
    else:
        image.save(out, "TIFF", compression="tiff_lzw", dpi=(dpi, dpi))
    return out.getvalue()


def pdf(svg):
    from reportlab.graphics import renderPDF
    from svglib.svglib import svg2rlg

    def font(match):
        families = [f.strip(" '\"") for f in match.group(1).split(",")]
        return f'font-family="{next((PDF_FONTS[f] for f in families if f in PDF_FONTS), "Helvetica")}"'

    svg = re.sub(r'font-family="([^"]*)"', font, svg)
    drawing = svg2rlg(BytesIO(svg.encode("utf-8")))
    if drawing is None:
        raise ValueError("The figure couldn't be read as SVG")
    out = BytesIO()
    renderPDF.drawToFile(drawing, out)
    return out.getvalue()
