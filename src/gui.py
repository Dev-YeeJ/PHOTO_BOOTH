import os
import math
import time
import threading
from io import BytesIO
import tkinter.font as tkfont
import customtkinter as ctk
import cv2
import qrcode
from PIL import Image

from src.branding import Brand

# --- Branded palette (Freshmen First-Week Funfest / JITS) -------------------
# Two colours carry the identity, exactly as on the strip: paper white and the
# mascot's gold, over neutral black. Everything else is greyscale so the gold
# always reads as "this is the action".
BG        = "#0c0c0d"   # app background
BG_PANEL  = "#151517"   # cards / panels
BG_PANEL2 = "#1d1d20"   # inset surfaces (log, result)
BORDER    = "#303035"   # hairline borders
INK       = "#0f0f11"   # comic frame black (photo_frame.border_color)
PAPER     = "#f5f5ed"   # keyline / paper white
CITE_GRN  = "#1b3624"   # the green outlining the brush numerals
ACCENT    = "#f5b301"   # gold (panther) — primary action
ACCENT_HV = "#ffc42b"   # brighter on hover; darkening reads as "disabled"
OK        = "#3f9c5c"   # upload succeeded
DANGER    = "#e05252"
TEXT      = "#f2f2ee"   # warm white, matched to the paper
TEXT_DIM  = "#9b9b95"

# Comic frame proportions, scaled down from the strip's photo frames
CARD_BORDER  = 5        # black outer border
CARD_KEYLINE = 3        # paper-white inner keyline
HEADER_H     = 118      # branded header band (design units)
FOOTER_H     = 66       # two lines plus the org logos

# Animated mascot on the guest screen. Frames are keyed and cached once at this
# pixel height, then scaled to whatever the stage allows.
MASCOT_RENDER_H = 320
MASCOT_MAX_W    = 200   # design units
MASCOT_STEP     = 2     # take every Nth frame of the clip (30fps clip -> 15fps)

# Countdown art is cached once at this pixel height and scaled down per screen
MAX_COUNT_ART_H = 520
COUNT_FRAC_OP   = 0.36  # share of the frame height the numeral takes
COUNT_FRAC_EXT  = 0.42
COUNT_WIDTH_CAP = 0.72  # SMILE is wide; never let it span the whole frame

EXT_HEADER_H = 96       # guest header carries the lockup alone
OP_PREVIEW_MAX_W = 460  # operator feed cap, design units

# Comic stickers scattered over the guest stage: asset, centre as a fraction of
# the stage, width in design units, and a phase offset for the idle pulse.
EXT_STICKERS = (
    ("1.png", 0.055, 0.11, 150, 0.00),
    ("5.png", 0.945, 0.12, 130, 0.30),
    ("3.png", 0.930, 0.88, 160, 0.60),
)


def _crop_fill(frame, out_w, out_h):
    """
    Centre-crop `frame` to the target aspect and resize to fit, in cv2 — about
    five times faster than PIL's ImageOps.fit on a camera-sized frame, which
    matters when this runs on every preview tick.
    """
    h, w = frame.shape[:2]
    target, src = out_w / max(1, out_h), w / max(1, h)
    if src > target:                        # too wide: trim the sides
        keep = max(1, int(round(h * target)))
        x0 = (w - keep) // 2
        frame = frame[:, x0:x0 + keep]
    elif src < target:                      # too tall: trim top and bottom
        keep = max(1, int(round(w / target)))
        y0 = (h - keep) // 2
        frame = frame[y0:y0 + keep]
    return cv2.resize(frame, (max(1, out_w), max(1, out_h)),
                      interpolation=cv2.INTER_AREA)


def _rgb(hex_color):
    """'#0c0c0d' -> (12, 12, 13)"""
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _comic_card(parent, fill=BG_PANEL, border=CARD_BORDER, keyline=CARD_KEYLINE):
    """
    A panel framed like the photos on the strip: black border, paper keyline,
    square corners. Returns (outer, inner) — grid/pack the outer, fill the inner.
    Pass keyline=0 for structural panels; the full frame is for media surfaces,
    the same way only the photos are framed on the strip.
    """
    outer = ctk.CTkFrame(parent, fg_color=INK, corner_radius=0)
    if keyline > 0:
        mid = ctk.CTkFrame(outer, fg_color=PAPER, corner_radius=0)
        mid.pack(fill="both", expand=True, padx=border, pady=border)
        inner = ctk.CTkFrame(mid, fg_color=fill, corner_radius=0)
        inner.pack(fill="both", expand=True, padx=keyline, pady=keyline)
    else:
        inner = ctk.CTkFrame(outer, fg_color=fill, corner_radius=0)
        inner.pack(fill="both", expand=True, padx=border, pady=border)
    return outer, inner

# The layout preview is rendered once at this height and then scaled to whatever
# room each screen has, so window resizing never triggers a re-render.
STRIP_RENDER_H = 1000


# Font stacks, resolved against what's actually installed at startup. The
# display stack is ordered by how close each face is to the strip's heavy
# condensed title; a font dropped into assets/brand/ overrides all of it.
DISPLAY_STACK = ("Impact", "Haettenschweiler", "Franklin Gothic Heavy", "Arial Black")
UI_STACK      = ("Segoe UI Variable Text", "Segoe UI", "Tahoma")
MONO_STACK    = ("Cascadia Mono", "Consolas", "Courier New")

_DISPLAY_FAMILY = "Arial Black"
_UI_FAMILY = "Segoe UI"
_MONO_FAMILY = "Consolas"


def set_display_family(family):
    global _DISPLAY_FAMILY
    _DISPLAY_FAMILY = family


def resolve_families(widget):
    """Pick the first installed family from each stack."""
    global _DISPLAY_FAMILY, _UI_FAMILY, _MONO_FAMILY
    available = set(tkfont.families(widget))
    for stack, name in ((DISPLAY_STACK, "_DISPLAY_FAMILY"),
                        (UI_STACK, "_UI_FAMILY"),
                        (MONO_STACK, "_MONO_FAMILY")):
        for family in stack:
            if family in available:
                globals()[name] = family
                break


def _tracked(text, space=" "):
    """Letter-spaced caps for section labels — Tk fonts have no tracking."""
    return space.join(text)


def _display_font(size, weight="bold"):
    # Heavy display face to echo the bold event title
    return ctk.CTkFont(family=_DISPLAY_FAMILY, size=size, weight=weight)


def _ui_font(size, weight="normal"):
    return ctk.CTkFont(family=_UI_FAMILY, size=size, weight=weight)


def _mono_font(size):
    return ctk.CTkFont(family=_MONO_FAMILY, size=size)


def _section(parent, text):
    """Section label: a gold tick plus letter-spaced caps."""
    row = ctk.CTkFrame(parent, fg_color="transparent")
    ctk.CTkFrame(row, fg_color=ACCENT, width=3, height=13, corner_radius=0).pack(side="left")
    ctk.CTkLabel(row, text=_tracked(text), font=_ui_font(11, "bold"),
                 text_color=TEXT_DIM).pack(side="left", padx=(8, 0))
    return row


class PhotoboothApp(ctk.CTk):
    def __init__(self, config, camera_handler, image_processor, uploader):
        super().__init__()

        self.config = config
        self.camera = camera_handler
        self.processor = image_processor
        self.uploader = uploader

        self.title("JITS Photobooth")
        # CTk multiplies geometry/widget sizes by the display's DPI factor, so
        # work in design units and clamp to what actually fits on screen.
        self.ui_scale = ctk.ScalingTracker.get_widget_scaling(self)
        max_w = int(self.winfo_screenwidth() / self.ui_scale) - 16
        max_h = int(self.winfo_screenheight() / self.ui_scale) - 64  # taskbar
        win_w, win_h = min(1400, max_w), min(860, max_h)
        # Offsets are raw screen pixels (CTk only scales the WxH part)
        pos_x = max(0, (self.winfo_screenwidth() - round(win_w * self.ui_scale)) // 2)
        pos_y = max(0, (self.winfo_screenheight() - round(win_h * self.ui_scale)) // 2)
        self.geometry(f"{win_w}x{win_h}+{pos_x}+{pos_y}")
        self.minsize(min(1040, win_w), min(660, win_h))
        ctk.set_appearance_mode("dark")
        self.configure(fg_color=BG)

        self.raw_dir = os.path.join(os.getcwd(), "data", "raw")
        self.final_dir = os.path.join(os.getcwd(), "data", "final")
        self.bg_path = os.path.join(os.getcwd(), "assets", "background.png")

        self.captured_photos = []
        self.is_capturing = False
        self.showing_result = False
        self.preview_window = None
        self._ext_resume_id = None
        # How long the finished photo / QR code stays on the guest display
        self.result_display_ms = int(config.get("result_display_seconds", 12) * 1000)
        # Horizontal flip, applied to the preview AND the saved photo so the two
        # can never disagree. Set "mirror": false in config.json to turn it off.
        self.mirror = bool(config.get("mirror", config.get("mirror_preview", True)))
        # Aspect of a printed photo slot, used to frame the guest preview
        slots = config.get("photo_slots") or []
        self.print_aspect = (slots[0]["width"] / slots[0]["height"]) if slots else 1.47
        # Guests scan through to the shared Drive folder when one is configured;
        # otherwise the QR falls back to the direct link for their own photo.
        self.gdrive_folder_url = (config.get("gdrive_folder_url")
                                  or os.environ.get("GDRIVE_FOLDER_URL") or "").strip()

        # --- Live layout preview state (mirrored on both screens) ----------
        # One render per tick at STRIP_RENDER_H, shown on the operator console
        # and the guest display, each scaled to its own panel.
        self.strip_photos = []      # captured photo paths for the current session
        self.strip_active = None    # slot index currently being captured
        self._strip_last = 0.0      # throttle timestamp
        self._strip_sig = None      # last rendered state, to skip idle redraws
        self._strip_ok = True       # cleared if rendering ever fails
        self.strip_interval = 0.25  # seconds between layout redraws
        # Display heights (design units); kept in sync with their panels on resize
        self._strip_h_op = max(240, win_h - 190)
        self._strip_h_ext = 520

        # --- Branding ------------------------------------------------------
        # Assets resolve from assets/brand/ first, then the strip artwork, so
        # exported logos / mascot / fonts light up without code changes.
        self.brand = Brand(config.get("assets_dir", "assets"), self.bg_path)
        resolve_families(self)
        family = self.brand.register_font()
        if family and family in tkfont.families(self):
            set_display_family(family)
        elif self.brand.font_path:
            print(f"Brand font found but Tk could not load family '{family}'; "
                  f"falling back to {_DISPLAY_FAMILY}.")
        self._header_w = 0
        self._ext_header_w = 0
        self._ext_footer_w = 0

        # Looping mascot clip (white background keyed out on load)
        self.ext_mascot_label = None
        self._mascot_frames = None
        self._mascot_interval = 83
        self._mascot_index = 0
        self._mascot_job = None

        # Countdown art burned into the preview frames
        self._count_token = ""
        self._count_scaled = {}

        # Guest stage geometry and its textured background
        self.ext_stage_frame = None
        self.ext_bg_label = None
        self._ext_stage_size = (0, 0)
        self._ext_strip_w = 140
        self._stage_base = None          # backdrop only, cached per size
        self._stage_bg = None            # backdrop + stickers, current frame
        self._mascot_display = None      # frames resized for the current layout
        self._mascot_phys = None
        self._mascot_patch = None        # background the mascot stands on

        self.setup_ui()
        self.open_preview_window()
        # Building the template layers costs ~1s; warm the cache off the UI thread
        threading.Thread(target=self._warm_strip_cache, daemon=True).start()
        threading.Thread(target=self._load_mascot_clip, daemon=True).start()
        self._start_mascot_when_ready()
        self.start_camera_preview()

        self.protocol("WM_DELETE_WINDOW", self.on_closing)

    def setup_ui(self):
        self.grid_columnconfigure(0, weight=3)
        self.grid_columnconfigure(1, weight=2)
        self.grid_columnconfigure(2, weight=0)
        self.grid_rowconfigure(0, weight=0)
        self.grid_rowconfigure(1, weight=1)

        # ---- Header: the event lockup, straight off the strip artwork -----
        self.header = ctk.CTkFrame(self, fg_color=BG, corner_radius=0, height=HEADER_H)
        self.header.grid(row=0, column=0, columnspan=3, sticky="ew")
        self.header.grid_propagate(False)
        self.header_label = ctk.CTkLabel(self.header, text="")
        self.header_label.pack(fill="both", expand=True)
        self.header.bind("<Configure>", lambda e: self._render_header(e.width))

        # ---- Left column: camera + controls ------------------------------
        left_outer, left = _comic_card(self, border=3, keyline=0)
        left_outer.grid(row=1, column=0, padx=(20, 10), pady=20, sticky="nsew")

        cam_head = ctk.CTkFrame(left, fg_color="transparent")
        cam_head.pack(fill="x", padx=20, pady=(16, 8))
        _section(cam_head, "LIVE CAMERA").pack(side="left")
        self.status_pill = ctk.CTkLabel(cam_head, text="READY", width=132, height=28,
                                        corner_radius=0, fg_color=ACCENT, text_color=INK,
                                        font=_display_font(14))
        self.status_pill.pack(side="right")

        cam_outer, cam_card = _comic_card(left, fill="#000000")
        cam_outer.pack(padx=18, pady=(0, 14), fill="both", expand=True)
        # Let the card take the space left over by the buttons rather than
        # growing to fit the video image (which would push them off-screen)
        cam_outer.pack_propagate(False)
        self.video_label = ctk.CTkLabel(cam_card, text="Starting camera…",
                                        font=_ui_font(15), text_color=TEXT_DIM)
        self.video_label.pack(expand=True, fill="both", padx=6, pady=6)
        self.countdown_label = ctk.CTkLabel(cam_card, text="",
                                            font=_display_font(96), text_color=ACCENT)
        self.countdown_label.place(relx=0.5, rely=0.5, anchor="center")

        self.start_btn = ctk.CTkButton(left, text="▶   START PHOTOBOOTH",
                                       font=_display_font(24), height=66, corner_radius=0,
                                       border_width=3, border_color=INK,
                                       fg_color=ACCENT, hover_color=ACCENT_HV,
                                       text_color=INK, command=self.start_session)
        self.start_btn.pack(padx=18, pady=(2, 8), fill="x")

        self.ext_screen_btn = ctk.CTkButton(left, text="🖥   Open Guest Display",
                                            font=_ui_font(14, "bold"), height=42, corner_radius=0,
                                            fg_color="transparent", border_width=2, border_color=BORDER,
                                            hover_color=BG_PANEL2, text_color=TEXT,
                                            command=self.open_preview_window)
        self.ext_screen_btn.pack(padx=18, pady=(0, 18), fill="x")

        # ---- Right column: status + result -------------------------------
        right_outer, right = _comic_card(self, border=3, keyline=0)
        right_outer.grid(row=1, column=1, padx=(10, 10), pady=20, sticky="nsew")

        _section(right, "ACTIVITY LOG").pack(anchor="w", padx=20, pady=(16, 6))
        self.status_text = ctk.CTkTextbox(right, height=180, corner_radius=0,
                                          fg_color=BG_PANEL2, border_width=1, border_color=BORDER,
                                          text_color=TEXT_DIM, font=_mono_font(12),
                                          state="disabled")
        self.status_text.pack(padx=20, pady=(0, 14), fill="x")

        _section(right, "LATEST PHOTO").pack(anchor="w", padx=20, pady=(2, 6))
        result_outer, result_card = _comic_card(right, fill=BG_PANEL2)
        result_outer.pack(padx=20, pady=(0, 12), fill="both", expand=True)
        result_outer.pack_propagate(False)
        self.result_label = ctk.CTkLabel(result_card, text="No photo yet",
                                         font=_ui_font(14), text_color=TEXT_DIM)
        self.result_label.pack(expand=True, fill="both", padx=8, pady=8)

        self.actions_frame = ctk.CTkFrame(right, fg_color="transparent")
        self.actions_frame.pack(fill="x", padx=20, pady=(0, 18))
        self.retry_btn = ctk.CTkButton(self.actions_frame, text="↻  Retry Uploads",
                                       font=_ui_font(13, "bold"), height=42, corner_radius=0,
                                       fg_color="transparent", border_width=2, border_color=OK,
                                       hover_color=BG_PANEL2, text_color=OK,
                                       command=self.retry_uploads)
        self.retry_btn.pack(side="left", padx=(0, 6), expand=True, fill="x")
        self.open_folder_btn = ctk.CTkButton(self.actions_frame, text="📁  Folder",
                                             font=_ui_font(13, "bold"), height=42, corner_radius=0,
                                             fg_color="transparent", border_width=2, border_color=BORDER,
                                             hover_color=BG_PANEL2, text_color=TEXT,
                                             command=self.open_final_folder)
        self.open_folder_btn.pack(side="right", padx=(6, 0), expand=True, fill="x")

        # ---- Third column: live layout preview ---------------------------
        strip_outer, strip_panel = _comic_card(self, border=3, keyline=0)
        strip_outer.grid(row=1, column=2, padx=(0, 20), pady=20, sticky="ns")
        strip_outer.bind("<Configure>", lambda e: self._on_strip_resize(e.height, "op"))

        _section(strip_panel, "LAYOUT PREVIEW").pack(anchor="w", padx=16, pady=(16, 6))
        self.strip_label = ctk.CTkLabel(strip_panel, text="Loading template…",
                                        font=_ui_font(12), text_color=TEXT_DIM)
        self.strip_label.pack(padx=16, pady=(0, 14), expand=True)

    def _set_status(self, text, color=ACCENT):
        """Operator-facing state pill: READY / SHOOTING / UPLOADING / SAVED …"""
        self.status_pill.configure(text=text, fg_color=color,
                                   text_color=PAPER if color == DANGER else INK)

    def log_status(self, message):
        self.status_text.configure(state="normal")
        self.status_text.insert("end", message + "\n")
        self.status_text.see("end")
        self.status_text.configure(state="disabled")

    def start_camera_preview(self):
        try:
            self.camera.start()
        except Exception as e:
            self.log_status(f"CAMERA ERROR: {e}")
            self.countdown_label.configure(text="NO CAMERA", font=_display_font(48), text_color=DANGER)
            self.start_btn.configure(state="disabled", text="CAMERA UNAVAILABLE",
                                     fg_color=BG_PANEL2, text_color=TEXT_DIM)
            self._set_status("NO CAMERA", DANGER)
            # No video loop to drive the layout preview — tick it ourselves
            self._idle_strip_loop()
            return
        self.update_video_stream()

    def _idle_strip_loop(self):
        """Keeps the layout preview alive when no camera feed is driving it."""
        self._update_layout_previews(None)
        self.after(500, self._idle_strip_loop)

    def _count_art(self, token):
        """The brush-lettered art for a countdown step, or None if there isn't one."""
        token = (token or "").strip().rstrip("!").lower()
        return self.brand.countdown(token, MAX_COUNT_ART_H) if token else None

    def _overlay_count(self, frame, art, frac):
        """
        Burn the countdown art into the preview frame. Compositing in PIL rather
        than stacking a transparent label over the video keeps the alpha correct
        and puts the numeral on the picture, where it belongs.
        """
        h = max(40, int(frame.height * frac))
        w = max(1, round(art.width * h / art.height))
        if w > frame.width * COUNT_WIDTH_CAP:  # SMILE is far wider than it is tall
            w = int(frame.width * COUNT_WIDTH_CAP)
            h = max(30, round(art.height * w / art.width))

        key = (id(art), w, h)
        scaled = self._count_scaled.get(key)
        if scaled is None:
            if len(self._count_scaled) > 12:
                self._count_scaled.clear()
            scaled = art.resize((w, h), Image.Resampling.LANCZOS)
            self._count_scaled[key] = scaled

        out = frame.convert("RGBA")
        out.alpha_composite(scaled, ((frame.width - w) // 2, (frame.height - h) // 2))
        return out.convert("RGB")

    def _set_countdown(self, text):
        # The art is composited into the video feed; the label only carries the
        # steps we have no art for, such as PROCESSING.
        self._count_token = (text or "").strip()
        has_art = self._count_art(self._count_token) is not None
        self.countdown_label.configure(text="" if has_art else self._count_token)

    def _set_ext_countdown(self, text):
        # Both screens share one token; either setter keeps them in step
        token = (text or "").strip()
        self._count_token = token
        if self.preview_window and self.preview_window.winfo_exists():
            has_art = self._count_art(token) is not None
            self.ext_countdown_label.configure(text="" if has_art else token)

    def _set_ext_instruction(self, text, color):
        if self.preview_window and self.preview_window.winfo_exists():
            self.ext_instruction_label.configure(text=text, text_color=color)

    def open_preview_window(self):
        if self.preview_window is None or not self.preview_window.winfo_exists():
            win = ctk.CTkToplevel(self)
            self.preview_window = win
            win.title("Photobooth — Guest Display")
            # Opens on the primary screen; drag it to the projector and hit F11
            gw = min(1300, int(win.winfo_screenwidth() / self.ui_scale) - 16)
            gh = min(900, int(win.winfo_screenheight() / self.ui_scale) - 64)
            win.geometry(f"{gw}x{gh}+8+8")
            win.configure(fg_color=BG)
            # F11 toggles fullscreen for event use; Esc exits it
            win.bind("<F11>", lambda e: win.attributes("-fullscreen", not win.attributes("-fullscreen")))
            win.bind("<Escape>", lambda e: win.attributes("-fullscreen", False))

            # Header — the event lockup, centred for the guest audience
            self.ext_header = ctk.CTkFrame(win, fg_color=BG, corner_radius=0,
                                           height=EXT_HEADER_H)
            self.ext_header.pack(fill="x")
            self.ext_header.pack_propagate(False)
            self.ext_header_label = ctk.CTkLabel(self.ext_header, text="")
            self.ext_header_label.pack(fill="both", expand=True)
            self.ext_header.bind("<Configure>", lambda e: self._render_ext_header(e.width))

            # Video stage. Everything here is positioned with place() so the
            # textured background can show between the pieces — a "transparent"
            # frame in Tk just repaints its parent's flat colour.
            self.ext_stage_frame = ctk.CTkFrame(win, fg_color=BG, corner_radius=0)
            self.ext_stage_frame.pack(expand=True, fill="both", padx=22, pady=(10, 6))
            self.ext_stage_frame.pack_propagate(False)

            self.ext_bg_label = ctk.CTkLabel(self.ext_stage_frame, text="")
            self.ext_bg_label.place(x=0, y=0, relwidth=1, relheight=1)

            # Live layout preview down the right-hand side of the guest screen
            self.ext_strip_card, strip_col = _comic_card(self.ext_stage_frame,
                                                        border=3, keyline=0)
            self.ext_strip_card.place(relx=1.0, rely=0.5, anchor="e")
            ctk.CTkLabel(strip_col, text=_tracked("YOUR STRIP"), font=_display_font(15),
                         text_color=ACCENT).pack(pady=(8, 5))
            self.ext_strip_label = ctk.CTkLabel(strip_col, text="")
            self.ext_strip_label.pack(padx=8, pady=(0, 8), expand=True)

            # Mascot animation, standing on the floor of the stage
            if self.brand.mascot_clip():
                self.ext_mascot_label = ctk.CTkLabel(self.ext_stage_frame, text="")
                self.ext_mascot_label.place(x=4, rely=1.0, y=-2, anchor="sw")

            # The frame hugs the picture, so the keyline reads as a photo frame
            self.ext_video_card, video_frame = _comic_card(self.ext_stage_frame,
                                                           fill="#000000")
            self.ext_video_card.place(relx=0.5, rely=0.5, anchor="center")
            self.ext_video_label = ctk.CTkLabel(video_frame, text="")
            self.ext_video_label.pack(expand=True, fill="both", padx=6, pady=6)
            self.ext_stage_frame.bind(
                "<Configure>", lambda e: self._layout_ext_stage(e.width, e.height))

            # Big countdown overlay
            self.ext_countdown_label = ctk.CTkLabel(video_frame, text="",
                                                    font=_display_font(200), text_color=ACCENT)
            self.ext_countdown_label.place(relx=0.5, rely=0.5, anchor="center")

            # Instruction line
            self.ext_instruction_label = ctk.CTkLabel(
                win, text="Get your props ready!  The operator will start the session soon.",
                font=_ui_font(20, "bold"), text_color=TEXT)
            self.ext_instruction_label.pack(pady=(0, 8))

            # Footer — the strip's paper-and-halftone band, rebuilt at UI size
            self.ext_footer = ctk.CTkFrame(win, fg_color=PAPER, corner_radius=0,
                                           height=FOOTER_H)
            self.ext_footer.pack(fill="x")
            self.ext_footer.pack_propagate(False)
            self.ext_footer_label = ctk.CTkLabel(self.ext_footer, text="")
            self.ext_footer_label.pack(fill="both", expand=True)
            self.ext_footer.bind("<Configure>", lambda e: self._render_ext_footer(e.width))

            # QR overlay (shown after upload so guests can scan to download their photo)
            self.ext_qr_frame = ctk.CTkFrame(win, fg_color="#ffffff", corner_radius=16)
            self.ext_qr_label = ctk.CTkLabel(self.ext_qr_frame, text="")
            self.ext_qr_label.pack(padx=14, pady=(14, 4))
            self.ext_qr_caption = ctk.CTkLabel(self.ext_qr_frame, text="Scan to download",
                                               font=_ui_font(15, "bold"), text_color="#111111")
            self.ext_qr_caption.pack(padx=14, pady=(0, 14))
            # Stays hidden until show_qr_on_external() places it

            # Force the strip and the bands to redraw into the new widgets
            self._strip_sig = None
            self._ext_header_w = 0
            self._ext_footer_w = 0
            self._start_mascot()
        else:
            self.preview_window.focus()

    # ---- Branded bands ---------------------------------------------------
    # Header and footer are rendered as images so they can use the real
    # artwork and the event font, then redrawn when the window width changes.

    def _band_image(self, label, width_px, height, builder, last_attr):
        w = int(width_px / self.ui_scale)
        if w < 200 or abs(w - getattr(self, last_attr)) < 12:
            return
        setattr(self, last_attr, w)
        try:
            img = builder((round(w * self.ui_scale), round(height * self.ui_scale)))
        except Exception as e:
            print(f"Brand band render failed: {e}")
            return
        ctkimg = ctk.CTkImage(light_image=img, dark_image=img, size=(w, height))
        label.configure(image=ctkimg, text="")
        label.image = ctkimg

    def _render_header(self, width_px):
        self._band_image(
            self.header_label, width_px, HEADER_H,
            lambda size: self.brand.header_band(
                size, subtitle="JITS PHOTOBOOTH  ·  OPERATOR CONSOLE"),
            "_header_w")

    def _render_ext_header(self, width_px):
        self._band_image(
            self.ext_header_label, width_px, EXT_HEADER_H,
            lambda size: self.brand.header_band(size, align="center", mascot=False),
            "_ext_header_w")

    def _render_ext_footer(self, width_px):
        self._band_image(
            self.ext_footer_label, width_px, FOOTER_H,
            lambda size: self.brand.paper_band(
                size,
                ["JUNIOR INFORMATION TECHNOLOGY SOCIETY   ·   "
                 "COLLEGE OF INFORMATION TECHNOLOGY EDUCATION",
                 "URDANETA CITY UNIVERSITY   ·   AY 2026-2027"],
                text_color=_rgb(CITE_GRN), logos=("jits", "cite", "ucu")),
            "_ext_footer_w")

    def _fit_box(self, label, aspect_ratio, fallback, chrome=8):
        """Largest width/height (design units) with `aspect_ratio` that fits `label`."""
        avail_w = int(label.winfo_width() / self.ui_scale) - chrome
        avail_h = int(label.winfo_height() / self.ui_scale) - chrome
        if avail_w < 80 or avail_h < 80:      # before the first layout pass
            avail_w, avail_h = fallback
        w = max(60, min(avail_w, int(avail_h * aspect_ratio)))
        return w, max(60, int(w / aspect_ratio))

    def update_video_stream(self):
        ret, frame = self.camera.get_frame(mirror=self.mirror)
        if ret:
            # Convert cv2 frame (BGR) to PIL image (RGB)
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            # Fit the preview to the camera card without pushing the buttons off
            h, w = frame_rgb.shape[:2]
            aspect_ratio = w / h
            new_w, new_h = self._fit_box(self.video_label, aspect_ratio, (560, 400))
            if new_w > OP_PREVIEW_MAX_W:      # Tk image conversion dominates here
                new_w = OP_PREVIEW_MAX_W
                new_h = max(120, round(new_w / aspect_ratio))
            frame_resized = cv2.resize(frame_rgb, (round(new_w * self.ui_scale),
                                                   round(new_h * self.ui_scale)))

            img = Image.fromarray(frame_resized)
            count_art = self._count_art(self._count_token)
            if count_art is not None:
                img = self._overlay_count(img, count_art, COUNT_FRAC_OP)
            imgtk = ctk.CTkImage(light_image=img, dark_image=img, size=(new_w, new_h))

            self.video_label.configure(image=imgtk, text="")
            self.video_label.image = imgtk

            # Keep the external display live too (including during the countdown),
            # unless we're currently showing the finished photo on it.
            if (not self.showing_result
                    and self.preview_window is not None
                    and self.preview_window.winfo_exists()):
                # Guests see the feed cropped to the printed photo's aspect, so
                # the preview is exactly the frame that lands on the strip —
                # and it fills the card instead of floating in black bars.
                ext_w, ext_h = self._ext_box_for(self.print_aspect)
                img_ext = Image.fromarray(_crop_fill(frame_rgb,
                                                    round(ext_w * self.ui_scale),
                                                    round(ext_h * self.ui_scale)))
                if count_art is not None:
                    img_ext = self._overlay_count(img_ext, count_art, COUNT_FRAC_EXT)
                imgtk_ext = ctk.CTkImage(light_image=img_ext, dark_image=img_ext, size=(ext_w, ext_h))
                self.ext_video_label.configure(image=imgtk_ext, text="")
                self.ext_video_label.image = imgtk_ext

        self._update_layout_previews(img if ret else None)
        self.after(55, self.update_video_stream)

    def _warm_strip_cache(self):
        # Rendering the template layers costs ~1s the first time; do it up front
        # so the first live layout frame doesn't stall the UI.
        try:
            self.processor.build_strip_preview([], self.bg_path, target_h=STRIP_RENDER_H)
        except Exception as e:
            print(f"Layout preview warm-up failed: {e}")

    # ---- Guest stage: textured background, comic elements, placement -----

    def _layout_ext_stage(self, width_px, height_px):
        """
        Recompute the guest stage on resize: how big the feed can be, where the
        photo frame sits between the mascot and the strip, and the background.
        """
        w = int(width_px / self.ui_scale)
        h = int(height_px / self.ui_scale)
        if w < 200 or h < 150:
            return
        if (w, h) == self._ext_stage_size:
            return
        self._ext_stage_size = (w, h)

        # The strip is sized by the stage, not by its own card — the card wraps
        # its image, so reading its height back would just re-affirm itself.
        self._strip_h_ext = max(200, h - 64)   # heading, padding and border
        self._strip_sig = None

        # Its width follows from the strip's own aspect, which we know exactly.
        # Measuring the card instead would read a stale value on the first pass.
        strip_aspect = (self.processor.canvas_width / self.processor.canvas_height
                        if self.processor.canvas_height else 1 / 3)
        self._ext_strip_w = round(self._strip_h_ext * strip_aspect) + 24
        mascot_w = MASCOT_MAX_W if self.ext_mascot_label is not None else 0

        # Centre the photo frame in the space the mascot and strip leave.
        # Two traps here: Tk ADDS relx/rely to x/y, so the original relx=0.5
        # must be cleared, and CTk scales place() but not place_configure() —
        # so this has to go through place() to land in design units.
        left = mascot_w + 10
        right = w - self._ext_strip_w - 12
        self.ext_video_card.place(relx=0, rely=0, x=(left + right) // 2,
                                  y=h // 2, anchor="center")

        self._mascot_display = None        # force the mascot to re-scale
        self._render_stage_bg()

    def _ext_box_for(self, aspect):
        """Largest box of `aspect` that fits the free middle of the guest stage."""
        w, h = self._ext_stage_size
        mascot_w = MASCOT_MAX_W if self.ext_mascot_label is not None else 0
        avail_w = max(140, w - mascot_w - self._ext_strip_w - 34) - 28
        avail_h = h - 28
        bw = max(120, min(avail_w, int(avail_h * aspect)))
        return bw, max(90, round(bw / aspect))

    def _render_stage_bg(self):
        """
        The swirl backdrop behind the stage. Drawn once per size — turning a
        stage-sized image into a Tk PhotoImage costs ~60ms, far too much to do
        on an animation tick, so the moving parts are separate small labels.
        """
        if self.ext_bg_label is None or not self.ext_bg_label.winfo_exists():
            return
        w, h = self._ext_stage_size
        px = lambda v: max(1, round(v * self.ui_scale))

        base = self.brand.backdrop((px(w), px(h)), dim=0.13, vignette=0.9)
        img = (base.convert("RGBA") if base is not None
               else Image.new("RGBA", (px(w), px(h)), _rgb(BG) + (255,)))

        # Comic stickers composited straight in: static, so they cost nothing
        # per frame, and the mascot picks them up in its background patch.
        for asset, fx, fy, sw, _phase in EXT_STICKERS:
            art = self.brand.sticker(asset, px(sw))
            if art is None:
                continue
            x = min(max(0, round(px(w) * fx - art.width / 2)), img.width - art.width)
            y = min(max(0, round(px(h) * fy - art.height / 2)), img.height - art.height)
            img.alpha_composite(art, (x, y))

        self._stage_bg = img
        ctkimg = ctk.CTkImage(light_image=img, dark_image=img, size=(w, h))
        self.ext_bg_label.configure(image=ctkimg, text="")
        self.ext_bg_label.image = ctkimg
        self._mascot_patch = None

    # ---- Animated mascot -------------------------------------------------

    def _load_mascot_clip(self):
        """Key the mascot clip off the UI thread; it takes a second or two."""
        try:
            loaded = self.brand.mascot_frames(MASCOT_RENDER_H, step=MASCOT_STEP)
        except Exception as e:
            print(f"Mascot clip could not be loaded: {e}")
            return
        if not loaded:
            return
        self._mascot_frames, self._mascot_interval = loaded

    def _start_mascot_when_ready(self, attempts=75):
        """
        Poll for the loader thread from the UI thread. The thread can't schedule
        this itself — it finishes before mainloop() starts, and .after() from a
        non-main thread raises until the loop is running.
        """
        if self._mascot_frames:
            self._start_mascot()
        elif attempts > 0:
            self.after(400, self._start_mascot_when_ready, attempts - 1)

    def _start_mascot(self):
        if self._mascot_job is None and self._mascot_frames:
            self._tick_mascot()

    def _mascot_size(self, src):
        """Mascot size in design units: capped by width, then by stage height."""
        _, stage_h = self._ext_stage_size
        mh = round((MASCOT_MAX_W - 6) * src.height / src.width)
        if stage_h:
            mh = min(mh, stage_h - 10)
        mh = max(100, mh)
        return max(1, round(src.width * mh / src.height)), mh

    def _tick_mascot(self):
        frames = self._mascot_frames
        label = self.ext_mascot_label
        if not frames or label is None or not label.winfo_exists():
            self._mascot_job = None
            return

        mw, mh = self._mascot_size(frames[0])
        phys = (max(1, round(mw * self.ui_scale)), max(1, round(mh * self.ui_scale)))
        if self._mascot_display is None or self._mascot_phys != phys:
            self._mascot_phys = phys
            self._mascot_display = [f.resize(phys, Image.Resampling.BILINEAR)
                                    for f in frames]
            self._mascot_patch = None

        # The mascot is keyed, so it needs the stage texture behind it to blend
        if self._mascot_patch is None:
            self._mascot_patch = self._stage_patch_at(4, mh, phys)

        frame = self._mascot_display[self._mascot_index % len(self._mascot_display)]
        self._mascot_index += 1

        base = (self._mascot_patch.copy() if self._mascot_patch is not None
                else Image.new("RGBA", phys, _rgb(BG) + (255,)))
        base.alpha_composite(frame)
        flat = base.convert("RGB")
        img = ctk.CTkImage(light_image=flat, dark_image=flat, size=(mw, mh))
        label.configure(image=img, text="")
        label.image = img

        self._mascot_job = self.after(self._mascot_interval, self._tick_mascot)

    def _stage_patch_at(self, x, bottom_gap, phys):
        """The slice of stage background sitting under a bottom-left element."""
        if self._stage_bg is None:
            return None
        x0 = round(x * self.ui_scale)
        y0 = self._stage_bg.height - round(2 * self.ui_scale) - phys[1]
        if y0 < 0 or x0 + phys[0] > self._stage_bg.width:
            return None
        return self._stage_bg.crop((x0, y0, x0 + phys[0], y0 + phys[1]))

    def _on_strip_resize(self, panel_height, which):
        """Keep the layout preview filling its panel as windows resize / go fullscreen."""
        # heading + padding + the comic frame's border and keyline
        chrome = (62 if which == "op" else 46) + 6
        h = max(220, int(panel_height / self.ui_scale) - chrome)
        current = self._strip_h_op if which == "op" else self._strip_h_ext
        if abs(h - current) < 8:               # ignore layout jitter
            return
        if which == "op":
            self._strip_h_op = h
        else:
            self._strip_h_ext = h
        self._strip_sig = None                 # force a redraw at the new size

    def _update_layout_previews(self, live_img=None):
        """
        Redraw the layout strip — the real template with the photos taken so far,
        the live camera feed in the slot being shot, and placeholders for the
        rest — on both the operator console and the guest display.
        """
        if not self._strip_ok:
            return
        now = time.monotonic()
        if now - self._strip_last < self.strip_interval:
            return

        active = self.strip_active
        sig = (len(self.strip_photos), active, self._strip_h_op, self._strip_h_ext)
        # Only the active slot animates, so skip redraws when nothing changed
        if active is None and sig == self._strip_sig:
            return
        self._strip_last = now
        self._strip_sig = sig

        try:
            strip = self.processor.build_strip_preview(
                list(self.strip_photos), self.bg_path,
                active_index=active, target_h=STRIP_RENDER_H,
                live_frame=live_img if active is not None else None)
        except Exception as e:
            self._strip_ok = False
            self.log_status(f"Layout preview disabled: {e}")
            return

        aspect = strip.width / strip.height

        op_h = self._strip_h_op
        op_img = ctk.CTkImage(light_image=strip, dark_image=strip,
                              size=(max(1, round(op_h * aspect)), op_h))
        self.strip_label.configure(image=op_img, text="")
        self.strip_label.image = op_img

        if self.preview_window is not None and self.preview_window.winfo_exists():
            ext_h = self._strip_h_ext
            ext_img = ctk.CTkImage(light_image=strip, dark_image=strip,
                                   size=(max(1, round(ext_h * aspect)), ext_h))
            self.ext_strip_label.configure(image=ext_img, text="")
            self.ext_strip_label.image = ext_img

    def start_session(self):
        self.start_btn.configure(state="disabled")
        # Drop back to the live feed on the external display if a prior result is still shown
        self._clear_external_result()
        self.captured_photos = []
        # Reset the layout preview back to empty slots for the new group
        self.strip_photos = []
        self.strip_active = None
        self._strip_sig = None
        self.status_text.configure(state="normal")
        self.status_text.delete("1.0", "end")
        self.status_text.configure(state="disabled")
        self._set_status("SHOOTING")
        self.log_status("Starting new session...")

        # Start the sequence in a separate thread to keep GUI responsive
        threading.Thread(target=self.capture_sequence, daemon=True).start()

    def capture_sequence(self):
        self.is_capturing = True
        num_photos = len(self.config.get("photo_slots") or []) or 4
        countdown_sec = self.config.get("countdown_seconds", 3)

        self.after(0, self._set_ext_instruction, "Session starting!  Look at the camera!", TEXT)

        for i in range(num_photos):
            # Light up this slot in the layout preview on both screens
            self.strip_active = i
            # Countdown
            for c in range(countdown_sec, 0, -1):
                self.after(0, self._set_countdown, str(c))
                self.after(0, self._set_ext_countdown, str(c))
                time.sleep(1)

            self.after(0, self._set_countdown, "SMILE!")
            self.after(0, self._set_ext_countdown, "SMILE!")
            time.sleep(0.5)

            # Capture
            filepath = self.camera.capture_photo(self.raw_dir, mirror=self.mirror)
            if filepath:
                self.captured_photos.append(filepath)
                self.strip_photos.append(filepath)
                self.after(0, lambda i=i: self.log_status(f"Captured photo {i+1}/{num_photos}"))

            self.after(0, self._set_countdown, "")
            self.after(0, self._set_ext_countdown, "")
            time.sleep(0.5)

        self.strip_active = None
        self.after(0, self._set_status, "PROCESSING")
        self.after(0, self._set_countdown, "PROCESSING...")
        self.after(0, self._set_ext_countdown, "")
        self.after(0, self._set_ext_instruction, "Processing your photos... Please wait! ⌛", ACCENT)

        # Bail out cleanly if the camera returned nothing (e.g. it was unplugged mid-session)
        if not self.captured_photos:
            self.after(0, lambda: self.log_status("No photos captured — session aborted."))
            self.after(0, self._set_status, "NO PHOTOS", DANGER)
            self.after(0, self._set_countdown, "")
            self.after(0, lambda: self.start_btn.configure(state="normal"))
            self.is_capturing = False
            self.after(0, self.resume_external_live)
            return

        # Process photos
        final_path = self.processor.process_photos(self.captured_photos, self.bg_path, self.final_dir)
        self.after(0, lambda: self.log_status(f"Saved locally: {os.path.basename(final_path)}"))

        # Update result preview (operator panel + guest-facing external display)
        self.after(0, lambda: self.show_result_preview(final_path))
        self.after(0, lambda: self.show_result_on_external(final_path))
        # The folder QR is the same every session, so it doesn't wait on R2 —
        # guests still leave with a way to get their photos if the upload fails.
        if self.gdrive_folder_url:
            self.after(0, lambda: self.show_qr_on_external(self.gdrive_folder_url))

        # GDrive Copy
        success, gdrive_path = self.uploader.copy_to_gdrive(final_path)
        if success:
            self.after(0, lambda: self.log_status("Copied to Google Drive"))
        else:
            self.after(0, lambda: self.log_status(f"GDrive Error: {gdrive_path}"))

        # R2 Upload
        self.after(0, self._set_status, "UPLOADING")
        self.after(0, lambda: self.log_status("Uploading to R2..."))
        success, r2_result = self.uploader.upload_to_r2(final_path)
        if success:
            self.after(0, lambda: self.log_status(f"R2 Upload Success!\nURL: {r2_result}"))
            self.after(0, self._set_status, "SAVED", OK)
            # Only needed as the fallback target — the folder QR is already up
            if not self.gdrive_folder_url:
                self.after(0, lambda url=r2_result: self.show_qr_on_external(url))
        else:
            self.after(0, lambda: self.log_status(f"R2 Upload Failed: {r2_result}"))
            self.after(0, self._set_status, "UPLOAD FAILED", DANGER)

        self.after(0, self._set_countdown, "")
        self.after(0, lambda: self.start_btn.configure(state="normal"))
        self.is_capturing = False
        # The external display resumes its live feed via resume_external_live().

    def show_result_preview(self, filepath):
        try:
            img = Image.open(filepath)
            # Resize to fit the right panel
            size = self._fit_box(self.result_label, img.width / img.height, (300, 420))
            imgtk = ctk.CTkImage(light_image=img, dark_image=img, size=size)
            self.result_label.configure(image=imgtk, text="")
            self.result_label.image = imgtk
        except Exception as e:
            self.log_status(f"Preview error: {e}")

    def show_result_on_external(self, filepath):
        # Show the finished composite on the guest-facing display
        if not (self.preview_window and self.preview_window.winfo_exists()):
            return
        try:
            img = Image.open(filepath)
            # Fit the portrait composite within the external video area
            size = self._ext_box_for(img.width / img.height)
            imgtk = ctk.CTkImage(light_image=img, dark_image=img, size=size)
            self.showing_result = True
            self.ext_qr_frame.place_forget()  # clear any QR from a previous session
            self.ext_video_label.configure(image=imgtk, text="")
            self.ext_video_label.image = imgtk
            self.ext_instruction_label.configure(text="Here's your photo! 📸", text_color=TEXT)
            self._schedule_ext_resume()
        except Exception as e:
            self.log_status(f"External preview error: {e}")

    def show_qr_on_external(self, url):
        # Overlay a QR code of the photo's public URL so guests can scan to download it.
        # Only meaningful while the result photo is still on screen.
        if not (self.preview_window and self.preview_window.winfo_exists() and self.showing_result):
            return
        try:
            target = self.gdrive_folder_url or url
            qr_img = self._make_qr_image(target, size=200)
            qrtk = ctk.CTkImage(light_image=qr_img, dark_image=qr_img, size=qr_img.size)
            self.ext_qr_label.configure(image=qrtk)
            self.ext_qr_label.image = qrtk
            self.ext_qr_caption.configure(
                text="Scan for the photo folder" if self.gdrive_folder_url
                else "Scan to download")
            self.ext_qr_frame.place(relx=0.62, rely=0.46, anchor="center")
            self.ext_instruction_label.configure(
                text="Scan the QR code to get your photos!", text_color=TEXT)
            # Reset the countdown so the full window is measured from when the QR appears
            self._schedule_ext_resume()
        except Exception as e:
            self.log_status(f"QR code error: {e}")

    def _make_qr_image(self, data, size=200):
        qr = qrcode.QRCode(border=2, box_size=10)
        qr.add_data(data)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        # Normalize the qrcode wrapper to a plain PIL image and size it for the display
        buf = BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return Image.open(buf).convert("RGB").resize((size, size), Image.Resampling.NEAREST)

    def _schedule_ext_resume(self):
        if self._ext_resume_id is not None:
            self.after_cancel(self._ext_resume_id)
        self._ext_resume_id = self.after(self.result_display_ms, self.resume_external_live)

    def _clear_external_result(self):
        # Stop showing the result photo/QR and cancel any pending auto-resume timer
        self.showing_result = False
        if self._ext_resume_id is not None:
            self.after_cancel(self._ext_resume_id)
            self._ext_resume_id = None
        if self.preview_window and self.preview_window.winfo_exists():
            self.ext_qr_frame.place_forget()

    def resume_external_live(self):
        # Hand the external display back to the live feed. Skip the instruction reset if a
        # new session is already running so we don't clobber its on-screen prompts.
        self._clear_external_result()
        if not self.is_capturing:
            # Don't leave the previous group's photos on screen
            self.strip_photos = []
            self.strip_active = None
        if (self.preview_window and self.preview_window.winfo_exists()
                and not self.is_capturing):
            self.ext_instruction_label.configure(
                text="Get your props ready!  The operator will start the session soon.",
                text_color=TEXT_DIM
            )

    def retry_uploads(self):
        self.log_status("Retrying pending uploads...")
        threading.Thread(target=self._run_retry, daemon=True).start()

    def _run_retry(self):
        results = self.uploader.retry_pending_uploads()
        if not results:
            self.after(0, lambda: self.log_status("No pending uploads."))
            return

        for filepath, success, msg in results:
            filename = os.path.basename(filepath)
            status = "Success" if success else "Failed"
            self.after(0, lambda f=filename, s=status: self.log_status(f"Retry {f}: {s}"))

    def open_final_folder(self):
        os.makedirs(self.final_dir, exist_ok=True)
        # Windows specific
        os.startfile(self.final_dir)

    def on_closing(self):
        self.camera.stop()
        self.destroy()
