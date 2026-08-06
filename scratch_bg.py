"""
Placeholder-background generator.

This is a dev helper for producing a *placeholder* backdrop when you don't have
the real artwork handy. The real background lives at assets/new_background.png
(the swirl template with the title + footer baked in) and must NOT be clobbered
by this script, so it writes to assets/background_placeholder.png and refuses to
overwrite either of the real templates.

Layout (canvas size + photo slots) is read from config.json so the placeholder
stays in sync with the real template.
"""
from PIL import Image, ImageDraw, ImageFont
import json
import os

OUTPUT_PATH = "assets/background_placeholder.png"
PROTECTED_PATHS = ("assets/new_background.png", "assets/background.png")


def load_layout(config_path="config.json"):
    if os.path.exists(config_path):
        with open(config_path, "r") as f:
            cfg = json.load(f)
        size = cfg.get("canvas_size", {})
        return (
            size.get("width", 1080),
            size.get("height", 1920),
            cfg.get("photo_slots", []),
        )
    # Sensible defaults matching the current template
    return 1080, 1920, [
        {"x": 172, "y": 222, "width": 736, "height": 414},
        {"x": 172, "y": 681, "width": 736, "height": 414},
        {"x": 172, "y": 1140, "width": 736, "height": 414},
    ]


def create_bg(output_path=OUTPUT_PATH):
    # Never overwrite the real background artwork.
    for protected in PROTECTED_PATHS:
        if os.path.abspath(output_path) == os.path.abspath(protected):
            print(f"Refusing to overwrite {protected}. Choose a different output path.")
            return

    width, height, slots = load_layout()

    # Dark modern background color
    img = Image.new('RGB', (width, height), color=(20, 20, 25))
    draw = ImageDraw.Draw(img)

    try:
        # On windows, arial bold usually exists
        font = ImageFont.truetype("arialbd.ttf", 40)
    except IOError:
        font = ImageFont.load_default()

    text = "JUNIOR INFORMATION TECHNOLOGY SOCIETY"

    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        text_w = bbox[2] - bbox[0]
    except AttributeError:
        text_w = 800

    draw.text(((width - text_w) / 2, 60), text, fill=(255, 255, 255), font=font)

    # Draw photo slot borders as a visual aid (photos paste over these at runtime)
    for slot in slots:
        shape = [
            (slot["x"] - 5, slot["y"] - 5),
            (slot["x"] + slot["width"] + 5, slot["y"] + slot["height"] + 5),
        ]
        draw.rectangle(shape, outline=(60, 60, 80), width=5)

    os.makedirs('assets', exist_ok=True)
    img.save(output_path)
    print(f"Placeholder background created at {output_path}.")


if __name__ == "__main__":
    create_bg()
