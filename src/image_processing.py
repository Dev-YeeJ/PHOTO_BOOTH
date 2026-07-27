from PIL import Image, ImageOps, ImageDraw, ImageFilter, ImageFont
import os
import time
from pathlib import Path

from src.branding import display_font_path

# Accent used for the "active slot" highlight in the guest strip preview
ACCENT_RGB = (245, 179, 1)

class ImageProcessor:
    def __init__(self, config):
        self.config = config
        self.canvas_width = config.get("canvas_size", {}).get("width", 1200)
        self.canvas_height = config.get("canvas_size", {}).get("height", 3600)
        self.photo_slots = config.get("photo_slots", [])
        self.overlays = config.get("overlays", [])
        self.photo_frame = config.get("photo_frame") or {}
        self.assets_dir = config.get("assets_dir", "assets")
        # Cache trimmed overlay images so we only load/crop them once
        self._overlay_cache = {}
        self._font_cache = {}
        # Caches for the live layout preview (see _build_preview_layers)
        self._preview_layers_cache = {}
        self._placeholder_cache = {}
        self._thumb_cache = {}

    def _frame_inset(self):
        cfg = self.photo_frame
        if not cfg:
            return 0
        return int(cfg.get("border_width", 12)) + int(cfg.get("keyline_width", 6))

    def _font(self, size):
        if size not in self._font_cache:
            f = None
            brand_font = display_font_path(self.assets_dir)
            candidates = ([brand_font] if brand_font else []) + [
                "arialbd.ttf", "ariblk.ttf", "arial.ttf"]
            for name in candidates:
                try:
                    f = ImageFont.truetype(name, size)
                    break
                except Exception:
                    continue
            self._font_cache[size] = f or ImageFont.load_default()
        return self._font_cache[size]

    def _load_overlay(self, asset_name):
        """Load an overlay sticker, trimmed to its visible (non-transparent) content."""
        if asset_name in self._overlay_cache:
            return self._overlay_cache[asset_name]
        path = os.path.join(self.assets_dir, asset_name)
        img = Image.open(path).convert("RGBA")
        bbox = img.getbbox()  # tight box around non-transparent pixels
        if bbox:
            img = img.crop(bbox)
        self._overlay_cache[asset_name] = img
        return img

    def _paste_framed_photo(self, canvas, photo, slot):
        """
        Composite a single photo onto the canvas inside a bold comic-style frame:
        black outer border + light inner keyline + a soft drop shadow so the
        photo lifts off the busy background. Falls back to a plain paste if no
        photo_frame is configured. The slot rectangle always defines the visible
        photo area; the frame grows outward from it.
        """
        cfg = self.photo_frame
        if not cfg:
            canvas.paste(photo, (slot["x"], slot["y"]), photo)
            return

        bw = int(cfg.get("border_width", 12))          # black outer border
        kw = int(cfg.get("keyline_width", 6))           # light inner keyline
        radius = int(cfg.get("corner_radius", 0))
        bc = tuple(cfg.get("border_color", [15, 15, 18])) + (255,)
        kc = tuple(cfg.get("keyline_color", [244, 244, 238])) + (255,)
        inset = bw + kw

        pw, ph = slot["width"], slot["height"]
        W, H = pw + 2 * inset, ph + 2 * inset

        tile = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        draw = ImageDraw.Draw(tile)
        if radius > 0:
            draw.rounded_rectangle([0, 0, W - 1, H - 1], radius=radius, fill=bc)
            draw.rounded_rectangle([bw, bw, W - 1 - bw, H - 1 - bw],
                                   radius=max(0, radius - bw), fill=kc)
        else:
            draw.rectangle([0, 0, W, H], fill=bc)
            draw.rectangle([bw, bw, W - bw, H - bw], fill=kc)

        # Drop the photo into the window (rounded inner corners if requested)
        if radius > 0:
            inner_r = max(0, radius - inset)
            mask = Image.new("L", (pw, ph), 0)
            ImageDraw.Draw(mask).rounded_rectangle([0, 0, pw - 1, ph - 1],
                                                   radius=inner_r, fill=255)
            tile.paste(photo, (inset, inset), mask)
        else:
            tile.paste(photo, (inset, inset), photo)

        pos = (slot["x"] - inset, slot["y"] - inset)

        # Soft drop shadow built from the tile's silhouette
        shadow_cfg = cfg.get("shadow")
        if shadow_cfg:
            ox, oy = shadow_cfg.get("offset", [0, 14])
            blur = int(shadow_cfg.get("blur", 22))
            opacity = int(shadow_cfg.get("opacity", 120))
            pad = blur * 2
            shadow = Image.new("RGBA", (W + 2 * pad, H + 2 * pad), (0, 0, 0, 0))
            silhouette = Image.new("RGBA", (W, H), (0, 0, 0, opacity))
            shadow.paste(silhouette, (pad, pad), tile.split()[3])
            shadow = shadow.filter(ImageFilter.GaussianBlur(blur))
            canvas.alpha_composite(shadow, (pos[0] - pad + int(ox), pos[1] - pad + int(oy)))

        canvas.alpha_composite(tile, pos)

    def _apply_overlays(self, canvas):
        """Composite the decorative sticker assets (1-6.png) on top of the photos."""
        for ov in self.overlays:
            try:
                sticker = self._load_overlay(ov["asset"])
            except Exception as e:
                print(f"Error loading overlay {ov.get('asset')}: {e}")
                continue

            # Scale to the requested width, preserving aspect ratio
            target_w = ov.get("width", sticker.width)
            scale = target_w / sticker.width
            target_h = max(1, round(sticker.height * scale))
            sticker = sticker.resize((target_w, target_h), Image.Resampling.LANCZOS)

            # Position by center point (cx, cy)
            x = round(ov.get("cx", 0) - target_w / 2)
            y = round(ov.get("cy", 0) - target_h / 2)
            canvas.alpha_composite(sticker, (x, y))

    # ---- Live layout preview --------------------------------------------
    # The preview is redrawn several times a second while a session runs, so the
    # costly parts (background, frame chrome + shadows, stickers) are rendered
    # once at full canvas size, downscaled and cached. Each frame then only
    # pastes photos / the live camera feed into the scaled slot rectangles.

    def _make_placeholder_tile(self, w, h, number, active):
        """A stand-in for an empty photo slot in the layout preview."""
        key = (w, h, number, active)
        if key in self._placeholder_cache:
            return self._placeholder_cache[key]

        tile = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(tile)
        draw.rectangle([0, 0, w, h], fill=(44, 38, 14, 240) if active else (24, 24, 30, 240))

        num_color = ACCENT_RGB + (255,) if active else (120, 120, 134, 255)
        num_font = self._font(max(10, int(h * 0.36)))
        lbl_font = self._font(max(8, int(h * 0.085)))

        num_txt = str(number)
        nb = draw.textbbox((0, 0), num_txt, font=num_font)
        draw.text(((w - (nb[2] - nb[0])) / 2 - nb[0],
                   (h * 0.44) - (nb[3] - nb[1]) / 2 - nb[1]),
                  num_txt, font=num_font, fill=num_color)

        label = "GET READY!" if active else f"PHOTO {number}"
        lb = draw.textbbox((0, 0), label, font=lbl_font)
        draw.text(((w - (lb[2] - lb[0])) / 2 - lb[0], h * 0.72),
                  label, font=lbl_font, fill=num_color)

        self._placeholder_cache[key] = tile
        return tile

    def _draw_active_ring(self, canvas, rect, inset, width):
        """Accent outline around the slot currently being captured."""
        x, y, w, h = rect
        d = ImageDraw.Draw(canvas)
        d.rectangle([x - inset, y - inset, x + w + inset, y + h + inset],
                    outline=ACCENT_RGB + (255,), width=width)

    def _build_preview_layers(self, background_path, target_h):
        """
        Render (and cache) the static parts of the layout at `target_h`:
        a base layer (background + empty framed slots) and a sticker layer that
        goes back on top once the photos are in, plus the slot rectangles in
        preview coordinates.
        """
        key = (background_path, target_h)
        if key in self._preview_layers_cache:
            return self._preview_layers_cache[key]

        try:
            background = Image.open(background_path).convert("RGBA")
        except FileNotFoundError:
            background = Image.new("RGBA", (self.canvas_width, self.canvas_height), (20, 20, 25, 255))
        background = background.resize((self.canvas_width, self.canvas_height), Image.Resampling.LANCZOS)

        # Frame chrome only — the photo window is filled in per frame below
        for slot in self.photo_slots:
            blank = Image.new("RGBA", (slot["width"], slot["height"]), (0, 0, 0, 0))
            self._paste_framed_photo(background, blank, slot)

        stickers = Image.new("RGBA", (self.canvas_width, self.canvas_height), (0, 0, 0, 0))
        self._apply_overlays(stickers)

        scale = target_h / self.canvas_height
        out_w = max(1, round(self.canvas_width * scale))
        base = background.resize((out_w, target_h), Image.Resampling.LANCZOS)
        stickers = stickers.resize((out_w, target_h), Image.Resampling.LANCZOS)

        rects = [(round(s["x"] * scale), round(s["y"] * scale),
                  max(1, round(s["width"] * scale)), max(1, round(s["height"] * scale)))
                 for s in self.photo_slots]
        ring = (max(2, round((self._frame_inset() + 6) * scale)), max(2, round(10 * scale)))

        layers = (base, stickers, rects, ring)
        self._preview_layers_cache[key] = layers
        return layers

    def _fit_photo(self, src, size):
        """Fit a captured photo (path) or a live camera frame into a slot rectangle."""
        if isinstance(src, str):
            key = (src, size)
            if key not in self._thumb_cache:
                if len(self._thumb_cache) > 32:
                    self._thumb_cache.clear()
                img = Image.open(src).convert("RGB")
                self._thumb_cache[key] = ImageOps.fit(img, size, Image.Resampling.LANCZOS,
                                                      centering=(0.5, 0.5))
            return self._thumb_cache[key]
        # Live frames change every tick, so favour speed over resampling quality
        return ImageOps.fit(src.convert("RGB"), size, Image.Resampling.BILINEAR,
                            centering=(0.5, 0.5))

    def build_strip_preview(self, photos, background_path, active_index=None,
                            target_h=640, live_frame=None):
        """
        Render the photobooth strip using the real template (background + frames
        + stickers). Captured photos fill their slots, the active slot shows the
        live camera feed when one is passed, remaining slots show numbered
        placeholders. Returns an RGBA image `target_h` px tall.
        """
        base, stickers, rects, (ring_inset, ring_width) = self._build_preview_layers(
            background_path, target_h)

        canvas = base.copy()
        for i, rect in enumerate(rects):
            x, y, w, h = rect
            photo = photos[i] if photos and i < len(photos) else None
            if photo is None and i == active_index and live_frame is not None:
                photo = live_frame

            if photo is not None:
                canvas.paste(self._fit_photo(photo, (w, h)), (x, y))
            else:
                canvas.paste(self._make_placeholder_tile(w, h, i + 1, i == active_index), (x, y))

            if i == active_index:
                self._draw_active_ring(canvas, rect, ring_inset, ring_width)

        canvas.alpha_composite(stickers)
        return canvas

    def process_photos(self, raw_photos, background_path, output_dir):
        """
        Takes the raw photos, crops them to fit the slots, pastes them onto the
        background, then composites the decorative overlay stickers on top.
        """
        Path(output_dir).mkdir(parents=True, exist_ok=True)

        # Load background
        try:
            background = Image.open(background_path).convert("RGBA")
        except FileNotFoundError:
            # Fallback to white background if not found
            print(f"Warning: Background {background_path} not found. Using white canvas.")
            background = Image.new("RGBA", (self.canvas_width, self.canvas_height), (255, 255, 255, 255))

        # Ensure background is the right size
        background = background.resize((self.canvas_width, self.canvas_height), Image.Resampling.LANCZOS)

        # Process each photo
        for i, photo_path in enumerate(raw_photos):
            if i >= len(self.photo_slots):
                break

            slot = self.photo_slots[i]
            slot_size = (slot["width"], slot["height"])

            # Load raw photo
            try:
                photo = Image.open(photo_path).convert("RGBA")
            except Exception as e:
                print(f"Error loading photo {photo_path}: {e}")
                continue

            # Crop to fill slot without distortion
            fitted_photo = ImageOps.fit(photo, slot_size, Image.Resampling.LANCZOS, centering=(0.5, 0.5))

            # Paste onto background inside its frame
            self._paste_framed_photo(background, fitted_photo, slot)

        # Composite the decorative stickers (1-6.png) over the photos
        self._apply_overlays(background)

        # Save final result
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        filename = f"photobooth_{timestamp}.jpg"
        filepath = os.path.join(output_dir, filename)
        
        # Convert to RGB to save as JPG
        final_image = background.convert("RGB")
        final_image.save(filepath, format="JPEG", quality=95)
        
        return filepath
