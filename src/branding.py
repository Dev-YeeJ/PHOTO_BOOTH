"""
Brand assets for the photobooth UI.

Everything the GUI needs in order to look like the printed strip is resolved
here, in priority order:

  1. a file exported into assets/brand/   (drop-in — no code change needed)
  2. a region derived from the strip artwork (assets/background.png)
  3. something drawn procedurally

So dropping title_lockup.png / mascot.png / a .ttf into assets/brand/ upgrades
the interface without touching gui.py. Anything still missing degrades to the
derived or drawn version instead of breaking.
"""
import os
import ctypes
import numpy as np
from PIL import Image, ImageDraw, ImageFont

BRAND_SUBDIR = "brand"
FONT_EXTS = (".ttf", ".otf", ".ttc")
VIDEO_EXTS = (".mp4", ".mov", ".webm", ".avi")
# Filenames checked for the looping mascot clip, in assets/brand/ then assets/
MASCOT_CLIPS = ("panther_loop", "mascot_loop", "mascot")

# The title lockup ("4 FRESHMEN FIRST-WEEK FUNFEST" + the three seals) as it
# sits inside the flattened strip artwork. Used until a transparent PNG of the
# lockup is exported into assets/brand/.
LOCKUP_BOX = (110, 35, 1130, 490)


def display_font_path(assets_dir="assets"):
    """Path of the first font file dropped into assets/brand/, if any."""
    d = os.path.join(assets_dir, BRAND_SUBDIR)
    if not os.path.isdir(d):
        return None
    for name in sorted(os.listdir(d)):
        if name.lower().endswith(FONT_EXTS):
            return os.path.join(d, name)
    return None


def _fade_edges(img, left=0.0, right=0.0, top=0.0, bottom=0.0):
    """Ramp the alpha to zero over the given fraction of each edge."""
    w, h = img.size
    alpha = img.split()[3] if img.mode == "RGBA" else Image.new("L", (w, h), 255)
    ramp = Image.new("L", (w, h), 255)
    px = ramp.load()
    lw, rw = int(w * left), int(w * right)
    th, bh = int(h * top), int(h * bottom)
    for x in range(w):
        fx = 255
        if lw and x < lw:
            fx = min(fx, int(255 * x / lw))
        if rw and x > w - rw:
            fx = min(fx, int(255 * (w - x) / rw))
        for y in range(h):
            f = fx
            if th and y < th:
                f = min(f, int(255 * y / th))
            if bh and y > h - bh:
                f = min(f, int(255 * (h - y) / bh))
            px[x, y] = f
    out = img.convert("RGBA")
    out.putalpha(Image.composite(ramp, Image.new("L", (w, h), 0), alpha)
                 if img.mode == "RGBA" else ramp)
    return out


def detect_flat_background(rgb, sample=6):
    """Guess whether a clip was rendered on white or on black, from its border."""
    edge = np.concatenate([rgb[:sample].reshape(-1, 3), rgb[-sample:].reshape(-1, 3),
                           rgb[:, :sample].reshape(-1, 3), rgb[:, -sample:].reshape(-1, 3)])
    return 255 if edge.mean() > 128 else 0


def key_flat_background(rgb, bg=None, seed_d=None, ramp=None):
    """
    Cut a subject off a flat white or black background and return RGBA.

    Works in "distance from the background colour" so the same logic handles a
    dark subject on white and a dark subject on black. Only background reachable
    from the frame border is removed, so enclosed matching pixels — the
    panther's teeth on white, or the shadow between its legs on black — survive.
    Edge pixels get a soft alpha and are un-premultiplied, leaving no fringe.
    """
    import cv2  # local: only needed when a clip is actually present

    if bg is None:
        bg = detect_flat_background(rgb)
    on_white = bg >= 128
    if seed_d is None:
        seed_d = 55 if on_white else 10
    if ramp is None:
        ramp = (10.0, 55.0) if on_white else (2.0, 26.0)
    ramp_lo, ramp_hi = ramp

    # Distance from the background colour: 0 = indistinguishable from backdrop
    dist = (255 - rgb.min(axis=2)) if on_white else rgb.max(axis=2)
    count, labels = cv2.connectedComponents((dist <= seed_d).astype(np.uint8))

    # Label lookup rather than np.isin — same result, a fraction of the cost
    edge = np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]])
    is_bg = np.zeros(count, dtype=bool)
    is_bg[np.unique(edge)] = True
    is_bg[0] = False

    # Soft edge, then hold everything not connected to the border fully opaque.
    # float32 in-place throughout: float64 temporaries dominate the runtime.
    alpha = dist.astype(np.float32)
    alpha -= np.float32(ramp_lo)
    alpha *= np.float32(1.0 / (ramp_hi - ramp_lo))
    np.clip(alpha, 0.0, 1.0, out=alpha)
    alpha[~is_bg[labels]] = 1.0

    a3 = alpha[..., None]
    out = rgb.astype(np.float32)
    out -= (np.float32(1.0) - a3) * np.float32(bg)      # undo the backdrop bleed
    out /= np.maximum(a3, np.float32(0.02))
    np.clip(out, 0.0, 255.0, out=out)
    out[alpha < 0.02] = 0.0

    rgba = np.empty(rgb.shape[:2] + (4,), np.uint8)
    rgba[..., :3] = out
    rgba[..., 3] = alpha * 255.0
    return rgba


def _luma_alpha(img, floor=16, gain=2.2):
    """
    Key a flattened crop against its own near-black backdrop: alpha follows
    brightness, so white artwork stays solid, the black swirl drops out, and
    the coloured seals are held in by their saturation.
    """
    a = np.asarray(img.convert("RGB")).astype(np.int16)
    lum = a.max(axis=2)
    sat = a.max(axis=2) - a.min(axis=2)
    alpha = np.clip((np.maximum(lum, sat * 2) - floor) * gain, 0, 255).astype(np.uint8)
    out = img.convert("RGBA")
    out.putalpha(Image.fromarray(alpha, mode="L"))
    return out


class Brand:
    def __init__(self, assets_dir="assets", background_path=None):
        self.assets_dir = assets_dir
        self.brand_dir = os.path.join(assets_dir, BRAND_SUBDIR)
        self.background_path = background_path or os.path.join(assets_dir, "background.png")
        self.font_path = display_font_path(assets_dir)
        self.font_family = None
        self._cache = {}

    # ---- exported assets ------------------------------------------------

    def exported(self, *stems):
        """Path of an exported brand asset, trying each stem in turn."""
        for stem in stems:
            for ext in (".png", ".webp", ".jpg", ".jpeg"):
                path = os.path.join(self.brand_dir, stem + ext)
                if os.path.exists(path):
                    return path
        return None

    def register_font(self):
        """
        Make a font dropped into assets/brand/ available to Tk for this process.
        Returns the family name, or None if there's nothing to register.
        """
        if not self.font_path:
            return None
        try:
            family = ImageFont.truetype(self.font_path, 20).getname()[0]
            FR_PRIVATE = 0x10
            ctypes.windll.gdi32.AddFontResourceExW(
                os.path.abspath(self.font_path), FR_PRIVATE, 0)
        except Exception as e:
            print(f"Could not register brand font {self.font_path}: {e}")
            return None
        self.font_family = family
        return family

    def truetype(self, size, fallbacks=("impact.ttf", "ariblk.ttf", "arialbd.ttf")):
        """A PIL font at `size`, preferring the brand font."""
        for name in ([self.font_path] if self.font_path else []) + list(fallbacks):
            try:
                return ImageFont.truetype(name, size)
            except Exception:
                continue
        return ImageFont.load_default()

    # ---- artwork-derived pieces -----------------------------------------

    def lockup(self, height):
        """
        The event title lockup at `height` px, transparent around the edges so
        it drops onto a dark header seamlessly.
        """
        key = ("lockup", height)
        if key in self._cache:
            return self._cache[key]

        path = self.exported("title_lockup", "lockup", "title")
        if path:
            img = Image.open(path).convert("RGBA")
            bbox = img.getbbox()
            if bbox:
                img = img.crop(bbox)
        else:
            # Crop it out of the flattened strip artwork and key it against its
            # own black backdrop, then feather what's left of the crop edges.
            img = Image.open(self.background_path).convert("RGB").crop(LOCKUP_BOX)
            img = _fade_edges(_luma_alpha(img), left=0.04, right=0.07,
                              top=0.05, bottom=0.07)

        w = max(1, round(img.width * height / img.height))
        img = img.resize((w, height), Image.Resampling.LANCZOS)
        self._cache[key] = img
        return img

    def mascot_clip(self):
        """Path of the looping mascot video, if one has been supplied."""
        for folder in (self.brand_dir, self.assets_dir):
            for stem in MASCOT_CLIPS:
                for ext in VIDEO_EXTS:
                    path = os.path.join(folder, stem + ext)
                    if os.path.exists(path):
                        return path
        return None

    def mascot_frames(self, height, step=2, background=None):
        """
        The mascot clip as a list of frames ready to animate, plus the interval
        between them in ms. White background is keyed out; `background` (an RGB
        tuple) flattens them for display, which keeps playback cheap.

        Returns (frames, interval_ms) or None when there's no clip.
        """
        import cv2

        path = self.mascot_clip()
        if not path:
            return None

        cached = self._load_frame_cache(path, height, step, background)
        if cached:
            return cached

        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            cap.release()
            return None
        fps = cap.get(cv2.CAP_PROP_FPS) or 24.0

        # Key at a little above the display size: the source is far larger than
        # we need, and keying cost scales with pixels.
        key_h = max(360, int(height * 1.2))

        keyed, i = [], 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if i % max(1, step) == 0:
                if frame.shape[0] > key_h:
                    scale = key_h / frame.shape[0]
                    frame = cv2.resize(frame, (round(frame.shape[1] * scale), key_h),
                                       interpolation=cv2.INTER_AREA)
                keyed.append(key_flat_background(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)))
            i += 1
        cap.release()
        if not keyed:
            return None

        # One bounding box across every frame, so the mascot doesn't drift or
        # jitter as its silhouette changes, and no empty margin is stored
        union = None
        for rgba in keyed:
            box = Image.fromarray(rgba[..., 3], mode="L").getbbox()
            if box is None:
                continue
            union = box if union is None else (min(union[0], box[0]), min(union[1], box[1]),
                                               max(union[2], box[2]), max(union[3], box[3]))
        frames = []
        for rgba in keyed:
            img = Image.fromarray(rgba, mode="RGBA")
            if union:
                img = img.crop(union)
            w = max(1, round(img.width * height / img.height))
            img = img.resize((w, height), Image.Resampling.LANCZOS)
            if background is not None:
                flat = Image.new("RGBA", img.size, tuple(background) + (255,))
                flat.alpha_composite(img)
                img = flat.convert("RGB")
            frames.append(img)

        interval = max(20, int(1000 * max(1, step) / fps))
        self._save_frame_cache(path, height, step, background, frames, interval)
        return frames, interval

    # Keyed frames are identical every launch, so keep them next to the clip
    # instead of paying the decode + key cost each time the app starts.

    def _frame_cache_path(self):
        return os.path.join(self.brand_dir, ".mascot_cache.npz")

    def _frame_cache_tag(self, clip, height, step, background):
        st = os.stat(clip)
        return f"v2|{os.path.basename(clip)}|{st.st_mtime_ns}|{st.st_size}|{height}|{step}|{background}"

    def _load_frame_cache(self, clip, height, step, background):
        path = self._frame_cache_path()
        if not os.path.exists(path):
            return None
        try:
            with np.load(path, allow_pickle=False) as data:
                if str(data["tag"]) != self._frame_cache_tag(clip, height, step, background):
                    return None
                stack, interval = data["frames"], int(data["interval"])
            mode = "RGB" if stack.shape[-1] == 3 else "RGBA"
            return [Image.fromarray(f, mode) for f in stack], interval
        except Exception as e:
            print(f"Ignoring unreadable mascot cache: {e}")
            return None

    def _save_frame_cache(self, clip, height, step, background, frames, interval):
        try:
            os.makedirs(self.brand_dir, exist_ok=True)
            np.savez_compressed(
                self._frame_cache_path(),
                tag=np.array(self._frame_cache_tag(clip, height, step, background)),
                frames=np.stack([np.asarray(f) for f in frames]),
                interval=np.array(interval))
        except Exception as e:
            print(f"Could not cache mascot frames: {e}")

    def mascot(self, height):
        """
        The panther mascot still. Prefers an exported cutout, otherwise keys the
        first frame of the looping clip.
        """
        key = ("mascot", height)
        if key in self._cache:
            return self._cache[key]
        path = self.exported("mascot", "panther", "mascot_full")
        if not path:
            return self._mascot_from_clip(height)
        img = Image.open(path).convert("RGBA")
        bbox = img.getbbox()
        if bbox:
            img = img.crop(bbox)
        w = max(1, round(img.width * height / img.height))
        img = img.resize((w, height), Image.Resampling.LANCZOS)
        self._cache[key] = img
        return img

    def _mascot_from_clip(self, height):
        """Still frame of the mascot, keyed out of the looping clip."""
        key = ("mascot_still", height)
        if key in self._cache:
            return self._cache[key]
        path = self.mascot_clip()
        if not path:
            return None
        try:
            import cv2
            cap = cv2.VideoCapture(path)
            ok, frame = cap.read()
            cap.release()
            if not ok:
                return None
            img = Image.fromarray(
                key_flat_background(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)), mode="RGBA")
        except Exception as e:
            print(f"Could not read mascot clip {path}: {e}")
            return None
        box = img.getbbox()
        if box:
            img = img.crop(box)
        w = max(1, round(img.width * height / img.height))
        img = img.resize((w, height), Image.Resampling.LANCZOS)
        self._cache[key] = img
        return img

    def _scaled_asset(self, filename, cache_key, height=None, width=None):
        """Load an asset from assets/, trim its transparency, scale to fit."""
        if cache_key in self._cache:
            return self._cache[cache_key]
        path = os.path.join(self.assets_dir, filename)
        if not os.path.exists(path):
            return None
        img = Image.open(path).convert("RGBA")
        box = img.getbbox()
        if box:
            img = img.crop(box)
        if height:
            width = max(1, round(img.width * height / img.height))
        else:
            height = max(1, round(img.height * width / img.width))
        img = img.resize((width, height), Image.Resampling.LANCZOS)
        self._cache[cache_key] = img
        return img

    def countdown(self, token, height):
        """
        The brush-lettered countdown art: token is 1-5 or 'smile'. Returns None
        for anything without a graphic, so callers can fall back to text.
        """
        token = str(token).lower()
        name = f"number{token}" if token.isdigit() else token
        return self._scaled_asset(f"{name}.png", ("countdown", name, height), height=height)

    def logo(self, name, height):
        """One of the org logos: 'jits', 'cite', 'ucu'."""
        return self._scaled_asset(f"{name}_logo.png", ("logo", name, height), height=height)

    def backdrop(self, size, dim=0.20, vignette=0.55):
        """
        The swirl artwork as an atmospheric layer: cover-cropped to `size`, then
        knocked back hard. At full strength it would swallow the interface —
        it's a texture behind the panels, not a picture.
        """
        key = ("backdrop", size, dim, vignette)
        if key in self._cache:
            return self._cache[key]
        path = os.path.join(self.assets_dir, "backdrop.png")
        if not os.path.exists(path):
            return None

        w, h = size
        src = Image.open(path).convert("RGB")
        scale = max(w / src.width, h / src.height)          # cover, never letterbox
        src = src.resize((max(1, round(src.width * scale)), max(1, round(src.height * scale))),
                         Image.Resampling.LANCZOS)
        left, top = (src.width - w) // 2, (src.height - h) // 2
        img = np.asarray(src.crop((left, top, left + w, top + h))).astype(np.float32) * dim

        if vignette:
            ys = np.linspace(-1, 1, h, dtype=np.float32)[:, None]
            xs = np.linspace(-1, 1, w, dtype=np.float32)[None, :]
            falloff = np.clip(1.0 - vignette * (xs * xs + ys * ys), 0, 1)
            img *= falloff[..., None]

        out = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8), "RGB")
        return self._store(key, out)

    STICKER_SRC_W = 560     # working copy size; the source art is far larger

    def sticker(self, name, width):
        """
        One of the comic stickers (1-6.png), trimmed and scaled to `width`.

        The trimmed source is kept at a modest working size, because these get
        asked for at many sizes while the idle animation runs — re-reading the
        original artwork each time costs about a hundred milliseconds apiece.
        """
        key = ("sticker", name, width)
        if key in self._cache:
            return self._cache[key]

        src_key = ("sticker_src", name)
        src = self._cache.get(src_key)
        if src is None:
            try:
                src = Image.open(os.path.join(self.assets_dir, name)).convert("RGBA")
            except Exception:
                return None
            bbox = src.getbbox()
            if bbox:
                src = src.crop(bbox)
            if src.width > self.STICKER_SRC_W:
                src = src.resize((self.STICKER_SRC_W,
                                  max(1, round(src.height * self.STICKER_SRC_W / src.width))),
                                 Image.Resampling.LANCZOS)
            self._cache[src_key] = src

        h = max(1, round(src.height * width / src.width))
        img = src.resize((width, h), Image.Resampling.LANCZOS)
        self._cache[key] = img
        return img

    # ---- drawn pieces ----------------------------------------------------

    def halftone(self, size, dot=(0, 0, 0, 46), spacing=16, radius=4.0,
                 fade="right", supersample=3):
        """
        A comic halftone dot field. `fade` thins the dots out towards that edge
        ("right", "left", "none") so it reads as texture rather than a pattern.
        """
        key = ("halftone", size, dot, spacing, radius, fade)
        if key in self._cache:
            return self._cache[key]

        w, h = size
        s = supersample
        tile = Image.new("RGBA", (w * s, h * s), (0, 0, 0, 0))
        draw = ImageDraw.Draw(tile)
        for gy in range(0, h + spacing, spacing):
            for gx in range(0, w + spacing, spacing):
                t = 1.0
                if fade == "right" and w:
                    t = 1.0 - gx / w
                elif fade == "left" and w:
                    t = gx / w
                t = max(0.0, min(1.0, t))
                r = radius * (0.35 + 0.65 * t) * s
                if r <= 0.4:
                    continue
                cx, cy = gx * s, gy * s
                draw.ellipse([cx - r, cy - r, cx + r, cy + r],
                             fill=dot[:3] + (int(dot[3] * (0.3 + 0.7 * t)),))
        tile = tile.resize((w, h), Image.Resampling.LANCZOS)
        self._cache[key] = tile
        return tile

    def _store(self, key, value):
        # Bands are cached per pixel size; drop the lot if a long resize session
        # fills it up rather than growing without bound.
        if len(self._cache) > 48:
            self._cache.clear()
        self._cache[key] = value
        return value

    def header_band(self, size, subtitle=None, align="left", bg=(14, 14, 17),
                    accent=(245, 179, 1), rule=4, mascot=True):
        """
        The window header as one image: the title lockup on the event's own
        black, a gold halftone fade, the mascot (once exported), and the gold
        rule along the bottom.
        """
        key = ("header", size, subtitle, align, bg, accent, rule, mascot)
        if key in self._cache:
            return self._cache[key]

        w, h = size
        # The swirl sits under the header as texture, knocked right back so the
        # lockup stays the only thing you read.
        swirl = self.backdrop((w, h), dim=0.28, vignette=0.35)
        band = swirl.convert("RGBA") if swirl else Image.new("RGBA", (w, h), bg + (255,))

        dots_w = min(w, max(240, w // 3))
        band.alpha_composite(
            self.halftone((dots_w, h), dot=accent + (58,), spacing=15,
                          radius=3.6, fade="left"), (w - dots_w, 0))
        if align == "center":
            # Mirror the texture so a centred lockup sits symmetrically
            band.alpha_composite(
                self.halftone((dots_w, h), dot=accent + (58,), spacing=15,
                              radius=3.6, fade="right"), (0, 0))

        art_h = h - rule
        mark = self.mascot(art_h - 6) if mascot else None
        if mark is not None:
            band.alpha_composite(mark, (w - mark.width - 28, 3))

        sub_font = self.truetype(max(10, int(h * 0.15)))
        draw = ImageDraw.Draw(band)

        if align == "center":
            sub_h = int(h * 0.22) if subtitle else 0
            lockup = self.lockup(max(20, art_h - 18 - sub_h))
            band.alpha_composite(lockup, ((w - lockup.width) // 2, 8))
            if subtitle:
                tb = draw.textbbox((0, 0), subtitle, font=sub_font)
                draw.text(((w - (tb[2] - tb[0])) / 2 - tb[0], 8 + lockup.height + 2),
                          subtitle, font=sub_font, fill=accent + (255,))
        else:
            lockup = self.lockup(max(20, art_h - 16))
            band.alpha_composite(lockup, (24, 8))
            if subtitle:
                tb = draw.textbbox((0, 0), subtitle, font=sub_font)
                draw.text((24 + lockup.width + 26, (art_h - (tb[3] - tb[1])) / 2 - tb[1]),
                          subtitle, font=sub_font, fill=accent + (255,))

        band.alpha_composite(Image.new("RGBA", (w, rule), accent + (255,)), (0, h - rule))
        return self._store(key, band)

    def paper_band(self, size, lines, text_color=(17, 17, 20), paper=(245, 245, 237),
                   gap=6, pad=18, logos=()):
        """
        The strip's white halftone footer, rebuilt at UI size: paper background,
        dot texture, the org logos, and the org lines set in the brand font.
        """
        key = ("band", size, tuple(lines), text_color, paper, tuple(logos))
        if key in self._cache:
            return self._cache[key]

        w, h = size
        band = Image.new("RGBA", (w, h), paper + (255,))
        band.alpha_composite(self.halftone((w, h), dot=(0, 0, 0, 40), spacing=14,
                                           radius=3.4, fade="left"))

        # Logos sit at the left; the text centres in what's left of the band
        logo_h = max(16, h - 10)
        text_left, text_right = pad, w - pad
        x = pad
        for name in logos:
            mark = self.logo(name, logo_h)
            if mark is None:
                continue
            band.alpha_composite(mark, (x, (h - mark.height) // 2))
            x += mark.width + 10
        if x > pad:
            text_left = x + 14

        draw = ImageDraw.Draw(band)
        n = max(1, len(lines))
        size_px = max(9, int((h - 2 * pad - gap * (n - 1)) / n))
        font = self.truetype(size_px)

        # Shrink until the longest line also fits the space beside the logos
        max_w = max(80, text_right - text_left)
        while size_px > 9:
            widest = max(draw.textlength(line, font=font) for line in lines)
            if widest <= max_w:
                break
            size_px = int(size_px * min(0.92, max_w / widest))
            font = self.truetype(size_px)

        centre = (text_left + text_right) / 2
        total = n * size_px + gap * (n - 1)
        y = (h - total) / 2
        for line in lines:
            tb = draw.textbbox((0, 0), line, font=font)
            draw.text((centre - (tb[2] - tb[0]) / 2 - tb[0], y - tb[1]),
                      line, font=font, fill=text_color + (255,))
            y += size_px + gap

        return self._store(key, band)
