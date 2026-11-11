"""Prepare the supplied vector artwork for inline, CSP-compatible animation.

Run with Python after replacing an original in chores/static/mascot. Group
numbers below refer to the original SVG's document order, visually mapped in
the artwork. Original exports are preserved. No bitmap tracing is involved.
"""
from copy import deepcopy
from pathlib import Path
import re
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "chores/static/mascot"
DEST = SOURCE / "animated"
PARTS = {
    "default": {0: "body", 10: "clipboard", 14: "check", 16: "check"},
    "reminder": {0: "body", 10: "clipboard", 14: "check", 16: "check"},
    "celebrate": {14: "body", **{i: "confetti" for i in range(2, 14)}},
    "question": {4: "body", 2: "thought", 3: "thought-dot"},
    "suggestion": {10: "body", 2: "bulb", 13: "book", **{i: "ray" for i in range(5, 10)}},
    "award": {5: "body", 11: "trophy", **{i: "ray" for i in range(2, 5)}},
}
# White eye shapes, with bounds measured from the vector paths. An eyelid
# overlay preserves shared whisker/face paths instead of deforming them.
EYES = {
    "default": [("cls-29", (162.69, 65.95, 56.78, 67.27)), ("cls-63", (287.34, 44.82, 32.14, 38.22))],
    "reminder": [("cls-30", (162.69, 91.82, 56.78, 67.27)), ("cls-66", (287.34, 70.69, 32.14, 38.22))],
    "suggestion": [("cls-21", (180.07, 125.96, 56.51, 69.66)), ("cls-23", (304.58, 115.04, 31.68, 45.12))],
    "question": [("cls-35", (126.87, 133.81, 55.28, 63.58)), ("cls-1", (232.19, 93.90, 31.87, 45.45))],
}


def prepare(pose):
    root = ET.parse(SOURCE / f"{pose}.svg").getroot()
    css = "".join(e.text or "" for e in root.iter() if e.tag.endswith("}style"))
    colors = dict(re.findall(r"\.(cls-\d+)\s*\{\s*fill:\s*([^;]+);", css))
    shapes = {e.get("class"): e for e in root.iter() if e.tag.endswith("}path")}
    groups = [e for e in root.iter() if e.tag.endswith("}g")]
    lids = []
    for cls, (x, y, w, h) in EYES.get(pose, []):
        shape = deepcopy(shapes[cls])
        shape.tag = "path"
        shape.attrib.pop("class", None)
        shape.set("fill", "#646672")
        shape.set("stroke", "#646672")
        shape.set("stroke-width", "10")
        lid = ET.Element("g", {"class": "mole-lid"})
        lid.append(shape)
        ET.SubElement(lid, "path", {"d": f"M{x + w*.15:.2f},{y+h*.53:.2f} Q{x+w*.5:.2f},{y+h*.72:.2f} {x+w*.86:.2f},{y+h*.49:.2f}",
                                   "fill": "none", "stroke": "#080708", "stroke-width": "4", "stroke-linecap": "round"})
        lids.append(lid)
    for child in list(root):
        if child.tag.rsplit("}", 1)[-1] in ("metadata", "defs", "rect"):
            root.remove(child)  # the unstyled, canvas-sized background rect
    for i, group in enumerate(groups):
        if part := PARTS[pose].get(i):
            group.set("class", f"mole-{part} mole-part-{i}")
            group.set("data-part", part)
    for e in root.iter():
        e.tag = e.tag.rsplit("}", 1)[-1]
        e.attrib.pop("id", None)  # exports contain no references to these IDs
        cls = e.get("class", "")
        if cls in colors:
            e.set("fill", colors[cls])
            del e.attrib["class"]
        if any("url(" in value or "href" in key for key, value in e.attrib.items()):
            raise ValueError(f"Review new SVG references before preparing {pose}.")
    body = groups[next(i for i, part in PARTS[pose].items() if part == "body")]
    body.extend(lids)
    root.set("xmlns", "http://www.w3.org/2000/svg")
    root.set("class", "mole-art")
    root.set("aria-hidden", "true")
    root.set("focusable", "false")
    # A little breathing room keeps small transforms inside a stable frame.
    _, _, width, height = map(float, root.get("viewBox").split())
    root.set("viewBox", f"-14 -14 {width+28:g} {height+28:g}")
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="unicode") + "\n"


if __name__ == "__main__":
    DEST.mkdir(exist_ok=True)
    for pose in PARTS:
        (DEST / f"{pose}.svg").write_text(prepare(pose), encoding="utf-8")
        print(f"Prepared {pose}")
