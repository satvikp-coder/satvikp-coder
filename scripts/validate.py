"""Check generated profile references and SVG parsing."""
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

root = Path(__file__).resolve().parents[1]
readme = (root / "README.md").read_text(encoding="utf-8")
for target in re.findall(r'<img[^>]+src="([^"]+)"', readme):
    file = root / target
    if not file.is_file():
        raise SystemExit(f"Missing image: {target}")
    ET.parse(file)
for file in (root / "assets").glob("*.svg"):
    ET.parse(file)
if readme.count("<!-- PROFILE:GENERATED:START -->") != 1 or readme.count("<!-- PROFILE:GENERATED:END -->") != 1:
    raise SystemExit("Invalid generated region")
print("README image paths and SVG syntax valid")
