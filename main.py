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
        "canvas_size": {"width": 1200, "height": 3600},
        "photo_slots": [
            {"x": 100, "y": 520, "width": 1000, "height": 680},
            {"x": 100, "y": 1390, "width": 1000, "height": 680},
            {"x": 100, "y": 2260, "width": 1000, "height": 680}
        ],
        "photo_frame": {
            "border_width": 14,
            "border_color": [17, 17, 20],
            "keyline_width": 7,
            "keyline_color": [244, 244, 238],
            "corner_radius": 0,
            "shadow": {"offset": [0, 16], "blur": 24, "opacity": 135}
        },
        "overlays": [
            {"asset": "1.png", "cx": 130, "cy": 525, "width": 260},
            {"asset": "2.png", "cx": 1055, "cy": 1160, "width": 220},
            {"asset": "3.png", "cx": 172, "cy": 1435, "width": 330},
            {"asset": "4.png", "cx": 1055, "cy": 2030, "width": 220},
            {"asset": "5.png", "cx": 155, "cy": 2305, "width": 230},
            {"asset": "6.png", "cx": 1055, "cy": 2900, "width": 190}
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
