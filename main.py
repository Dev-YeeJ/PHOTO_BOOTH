import json
import os
from src.camera import CameraHandler
from src.image_processing import ImageProcessor
from src.uploader import Uploader
from src.gui import PhotoboothApp

def load_config(config_path="config.json"):
    if os.path.exists(config_path):
        with open(config_path, "r") as f:
            return json.load(f)
    # Default config if not found
    return {
        "camera_index": 0,
        "countdown_seconds": 5,
        "mirror": True,
        "background_asset": "new_background.png",
        "lockup_box": [310, 5, 765, 292],
        "canvas_size": {"width": 1080, "height": 1920},
        # 16:9 exactly (46x16 by 46x9) — the camera's own aspect, so nothing is
        # ever cropped. As large as the artwork allows: the title ends at y~200
        # and the mascot starts at y~1590, and 16:9 ties the width to whatever
        # height is left between them.
        "photo_slots": [
            {"x": 172, "y": 222, "width": 736, "height": 414},
            {"x": 172, "y": 681, "width": 736, "height": 414},
            {"x": 172, "y": 1140, "width": 736, "height": 414}
        ],
        "photo_frame": {
            "border_width": 8,
            "border_color": [17, 17, 20],
            "keyline_width": 4,
            "keyline_color": [244, 244, 238],
            "corner_radius": 0,
            "shadow": {"offset": [0, 8], "blur": 12, "opacity": 125}
        },
        "overlays": [
            {"asset": "1.png", "cx": 187, "cy": 220, "width": 196},
            {"asset": "2.png", "cx": 881, "cy": 610, "width": 163},
            {"asset": "3.png", "cx": 202, "cy": 685, "width": 248},
            {"asset": "4.png", "cx": 881, "cy": 1069, "width": 163},
            {"asset": "5.png", "cx": 192, "cy": 1142, "width": 175},
            {"asset": "6.png", "cx": 876, "cy": 1528, "width": 136}
        ]
    }

def main():
    config = load_config()
    
    print("Initializing Camera...")
    camera = CameraHandler(camera_index=config.get("camera_index", 0))
    
    print("Initializing Image Processor...")
    processor = ImageProcessor(config)
    
    print("Initializing Uploader...")
    uploader = Uploader()
    
    print("Starting GUI...")
    app = PhotoboothApp(config, camera, processor, uploader)
    app.mainloop()

if __name__ == "__main__":
    main()
