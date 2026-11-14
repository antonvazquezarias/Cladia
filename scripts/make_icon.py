"""Makes cladia/static/cladia.ico (the window, shortcuts, installer and browser tab)
from installer/cladia-logo.png: cropped to the drawing, square, every Windows size."""
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]

logo = Image.open(ROOT / "installer" / "cladia-logo.png").convert("RGBA")
logo = logo.crop(logo.getchannel("A").getbbox())
side = max(logo.size) * 104 // 100  # a little room round it
square = Image.new("RGBA", (side, side))
square.paste(logo, ((side - logo.width) // 2, (side - logo.height) // 2))
square.save(ROOT / "cladia" / "static" / "cladia.ico", sizes=[(s, s) for s in SIZES])
