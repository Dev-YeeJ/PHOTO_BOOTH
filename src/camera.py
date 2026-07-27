import cv2
import time
import os
import threading
from pathlib import Path

class CameraHandler:
    def __init__(self, camera_index=0):
        self.camera_index = camera_index
        self.cap = None
        # Serializes cap.read()/release() across the preview and capture threads
        self._lock = threading.Lock()

    def start(self):
        if self.cap is None:
            self.cap = cv2.VideoCapture(self.camera_index)
            # Try to set high resolution
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
            
            if not self.cap.isOpened():
                raise RuntimeError(f"Could not open camera {self.camera_index}")

    def stop(self):
        with self._lock:
            if self.cap is not None:
                self.cap.release()
                self.cap = None

    def get_frame(self, mirror=False):
        """
        Grab a frame. Off by default so the preview matches what is actually
        saved; pass mirror=True (config: mirror_preview) for a mirror-like
        preview instead.
        """
        with self._lock:
            if self.cap is not None and self.cap.isOpened():
                ret, frame = self.cap.read()
                if ret:
                    if mirror:
                        frame = cv2.flip(frame, 1)
                    return True, frame
        return False, None

    def capture_photo(self, output_dir, prefix="raw", flush_frames=3, mirror=False):
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        # Drain a few buffered frames so we save the current moment, not a stale one
        for _ in range(flush_frames):
            self.get_frame(mirror=mirror)
        # Same mirror setting as the preview, so what guests see is what is saved
        ret, frame = self.get_frame(mirror=mirror)
        if ret:
            # Generate filename
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            filename = f"{prefix}_{timestamp}.jpg"
            filepath = os.path.join(output_dir, filename)
            
            # Save the frame
            cv2.imwrite(filepath, frame)
            return filepath
        return None
