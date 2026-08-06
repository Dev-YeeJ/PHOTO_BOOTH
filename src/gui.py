import os
import sys
import math
import time
import ctypes
import tkinter
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
# pixel height, then scaled to whatever the stage allows. Rendered with headroom
# above the largest size EXT_K_MAX can ask for, so a projector doesn't get a
# blown-up 320px mascot.
MASCOT_RENDER_H = 420
MASCOT_MAX_W    = 200   # design units at k = 1
MASCOT_STEP     = 2     # take every Nth frame of the clip (30fps clip -> 15fps)

# Countdown art is cached once at this pixel height and scaled down per screen
MAX_COUNT_ART_H = 720
COUNT_FRAC_OP   = 0.36  # share of the frame height the numeral takes
COUNT_FRAC_EXT  = 0.42
COUNT_WIDTH_CAP = 0.72  # SMILE is wide; never let it span the whole frame

EXT_HEADER_H = 96       # guest header carries the lockup alone, at k = 1
OP_PREVIEW_MAX_W = 460  # operator feed cap, design units

# --- Guest display, across screens ----------------------------------------
# The guest window is normally on a different monitor from the console, with a
# different resolution, aspect and Windows scaling factor. Every size below is
# quoted against this reference stage and multiplied by one factor `k` derived
# from the window, so the composition holds together on a 1366x768 laptop
# panel, a 1080p projector and a 4K TV without being re-tuned per venue.
EXT_REF_W, EXT_REF_H = 1280, 780
EXT_K_MIN, EXT_K_MAX = 0.55, 2.2

# The photo frame is what guests actually look at, so it keeps a guaranteed
# share of the stage width: the strip is trimmed and then the mascot is dropped
# before the frame is allowed to shrink past this.
EXT_VIDEO_MIN_FRAC = 0.34
EXT_MASCOT_MIN_W   = 110   # below this the mascot is hidden rather than squashed

# The QR card, shown beside the photo once the upload lands. Big enough to scan
# from across the room without taking the stage over.
QR_SIDE       = 260     # design units at k = 1
QR_MIN_SIDE   = 132     # below this a phone struggles from any distance
QR_CARD_PAD   = 14      # white margin around the code — quiet zone plus chrome
QR_MAX_W_FRAC = 0.26    # ceiling on the share of stage width the card may take

# Comic stickers scattered over the guest stage: asset, centre as a fraction of
# the stage, width in design units at k = 1, and a phase offset for the idle pulse.
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


def list_monitors():
    """
    Every attached monitor as a virtual-desktop rectangle, primary first.

    Tk only ever reports the primary screen's size, so this is the only way to
    put the guest window on the projector. Coordinates are raw pixels on the
    virtual desktop, which is exactly what the +x+y half of a geometry string
    wants. Windows only; returns [] elsewhere, and callers fall back to the
    primary screen.
    """
    if not sys.platform.startswith("win"):
        return []

    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", RECT),
                    ("rcWork", RECT), ("dwFlags", ctypes.c_ulong)]

    MONITORINFOF_PRIMARY = 1
    found = []

    def _collect(hmonitor, _hdc, _rect, _param):
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if ctypes.windll.user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
            full, work = info.rcMonitor, info.rcWork
            found.append({
                "x": full.left, "y": full.top,
                "w": full.right - full.left, "h": full.bottom - full.top,
                # Work area excludes the taskbar — used for the windowed size
                "wx": work.left, "wy": work.top,
                "ww": work.right - work.left, "wh": work.bottom - work.top,
                "primary": bool(info.dwFlags & MONITORINFOF_PRIMARY),
            })
        return 1

    # BOOL CALLBACK MonitorEnumProc(HMONITOR, HDC, LPRECT, LPARAM)
    proc = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
                              ctypes.POINTER(RECT), ctypes.c_void_p)
    try:
        ctypes.windll.user32.EnumDisplayMonitors(None, None, proc(_collect), 0)
    except Exception as e:
        print(f"Could not enumerate monitors: {e}")
        return []

    found.sort(key=lambda m: (not m["primary"], m["x"], m["y"]))
    return found


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
        layers = ((mid, border), (inner, keyline))
    else:
        inner = ctk.CTkFrame(outer, fg_color=fill, corner_radius=0)
        inner.pack(fill="both", expand=True, padx=border, pady=border)
        layers = ((inner, border),)
    # Recorded so the frame can be re-weighted for another screen; see _scale_comic_card
    outer._comic_layers = layers
    return outer, inner


def _scale_comic_card(outer, factor):
    """
    Re-pad a comic frame so its border keeps the same visual weight on a screen
    of a different size or DPI. `factor` folds the layout factor and the
    window's DPI scaling together, because pack_configure() — unlike CTk's
    pack() — takes raw pixels and applies no scaling of its own.
    """
    for widget, base in getattr(outer, "_comic_layers", ()):
        pad = max(1, round(base * factor))
        widget.pack_configure(padx=pad, pady=pad)

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
        # Strip artwork. "background_asset" swaps the template without touching
        # code — the layout that goes with it lives in canvas_size/photo_slots.
        self.bg_path = os.path.join(os.getcwd(), config.get("assets_dir", "assets"),
                                    config.get("background_asset", "new_background.png"))

        self.captured_photos = []
        self.is_capturing = False
        self.showing_result = False
        self.preview_window = None
        self._ext_resume_id = None
        self._closing = False       # set on quit so the polling loops stand down
        # How long the finished photo stays on the guest display
        self.result_display_ms = int(config.get("result_display_seconds", 12) * 1000)
        # The QR gets its own, longer window: guests have to get a phone out,
        # unlock it and open the camera before they can even aim. 0 leaves it up
        # until the operator starts the next session.
        self.qr_display_ms = int(config.get("qr_display_seconds", 30) * 1000)
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
        # Which screen the guest display opens on, and whether it goes fullscreen
        # there. "auto" means the projector when one is attached, and the primary
        # screen (windowed, so it can't trap the operator) when it isn't.
        self.guest_monitor_pref = config.get("guest_monitor", "auto")
        self.guest_fullscreen_pref = config.get("guest_fullscreen", "auto")

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
        self.brand = Brand(config.get("assets_dir", "assets"), self.bg_path,
                           config.get("lockup_box"))
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
        self._mascot_x = 4               # where it stands, so its patch matches

        # Guest layout state. `k` and the DPI factor are re-derived from the
        # window rather than stored once, because the guest display moves
        # between screens mid-event; these only record what's been applied so
        # the chrome isn't rebuilt on every tick.
        self._ext_k = 1.0
        self._ext_plan = None
        self._ext_stage_key = None
        self._ext_applied = (0.0, 0.0)
        self._ext_fonts = {}
        self._ext_header_h = EXT_HEADER_H
        self._ext_footer_h = FOOTER_H
        self._ext_result_path = None     # re-fitted if the screen changes
        self._ext_qr_url = None
        # Drives whether the stage reserves a column for the QR card
        self._ext_qr_showing = False
        self._guest_full = False         # borderless-fullscreen state, see _set_guest_fullscreen

        self.setup_ui()
        self.open_preview_window()
        # Building the template layers costs ~1s; warm the cache off the UI thread
        threading.Thread(target=self._warm_strip_cache, daemon=True).start()
        threading.Thread(target=self._load_mascot_clip, daemon=True).start()
        self._start_mascot_when_ready()
        self._watch_guest_display()
        self.start_camera_preview()

        # Same keys from the console, so a borderless guest display can always
        # be dismissed even if it has taken the keyboard focus
        self.bind("<F11>", lambda e: self._toggle_guest_fullscreen())
        self.bind("<Escape>", lambda e: self._toggle_guest_fullscreen(False))

        self.protocol("WM_DELETE_WINDOW", self.on_closing)

    # ---- Screen awareness ------------------------------------------------
    # CTk tracks DPI scaling per window and updates it live when a window is
    # dragged to another monitor, so nothing here may be cached at startup:
    # the console and the guest display routinely run at different factors.

    @staticmethod
    def _scaling_of(widget):
        """The DPI factor CTk is currently applying to `widget`'s window."""
        try:
            return ctk.ScalingTracker.get_widget_scaling(widget)
        except Exception:
            return 1.0

    @property
    def ui_scale(self):
        """DPI factor of the screen the operator console is on, right now."""
        return self._scaling_of(self)

    @property
    def ext_scale(self):
        """DPI factor of the screen the guest display is on, right now."""
        win = self.preview_window
        if win is not None and win.winfo_exists():
            return self._scaling_of(win)
        return self.ui_scale

    def _ext_metrics(self):
        """
        The two numbers the whole guest layout is derived from: the window's own
        DPI factor, and `k` — how big everything should be drawn given the room
        the window actually has, relative to the reference stage.
        """
        win = self.preview_window
        if win is None or not win.winfo_exists():
            return self.ui_scale, 1.0
        scale = self._scaling_of(win)
        w = max(1, win.winfo_width()) / scale
        h = max(1, win.winfo_height()) / scale
        k = min(w / EXT_REF_W, h / EXT_REF_H)
        return scale, max(EXT_K_MIN, min(EXT_K_MAX, k))

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

        # Opens the guest display, and once it's open sends it to the next
        # screen — the thing an operator actually needs when the projector is
        # plugged in after the app has started.
        self.ext_screen_btn = ctk.CTkButton(left, text="🖥   Guest Display  →  Next Screen",
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
        if self._closing:
            return
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

    # ---- Guest window placement -----------------------------------------

    def _guest_target(self):
        """
        Which monitor the guest display belongs on, and whether to fill it.

        Returns (monitor|None, fullscreen). On "auto" the projector is picked
        whenever a second screen is attached — that's the whole point of the
        guest display — and it's left windowed on a single-screen machine so
        the operator can never be locked out of the console.
        """
        screens = list_monitors()
        pref, full = self.guest_monitor_pref, self.guest_fullscreen_pref
        if isinstance(pref, str) and pref.strip().isdigit():   # "1" as well as 1
            pref = int(pref)

        if pref == "auto":
            target = screens[1] if len(screens) > 1 else (screens[0] if screens else None)
        elif isinstance(pref, int) and screens:
            target = screens[min(max(pref, 0), len(screens) - 1)]
        else:
            target = screens[0] if screens else None

        if full == "auto":
            full = len(screens) > 1
        return target, bool(full)

    def _guest_geometry_on(self, monitor, scale):
        """A centred windowed geometry string for `monitor`, in its own DPI."""
        win = self.preview_window
        if monitor is not None:
            ax, ay, aw, ah = monitor["wx"], monitor["wy"], monitor["ww"], monitor["wh"]
        else:
            ax, ay = 0, 0
            aw, ah = win.winfo_screenwidth(), win.winfo_screenheight()
        # geometry() scales the WxH half by the window's own DPI factor but
        # leaves +x+y in raw screen pixels, so the halves are computed apart.
        gw = max(480, int(min(1300 * scale, aw - 40) / scale))
        gh = max(360, int(min(900 * scale, ah - 40) / scale))
        px, py = round(gw * scale), round(gh * scale)
        return f"{gw}x{gh}+{ax + max(0, (aw - px) // 2)}+{ay + max(0, (ah - py) // 2)}"

    def _place_guest_window(self, monitor, fullscreen):
        """
        Move the guest window onto `monitor`, then size it there.

        The move has to land first: CTk only notices the new screen's DPI on its
        own polling loop, and until it does, any size we set is scaled by the
        old screen's factor.
        """
        win = self.preview_window
        if win is None or not win.winfo_exists():
            return
        # Drop out of borderless first: a window that has just become
        # overrideredirect ignores the position half of a geometry request, so
        # the move would silently not happen.
        if self._guest_full:
            self._set_guest_fullscreen(False)
        if monitor is not None:
            win.geometry(f"+{monitor['x'] + 40}+{monitor['y'] + 40}")
            win.update_idletasks()
        win.after(250, lambda: self._size_guest_window(monitor, fullscreen))

    def _size_guest_window(self, monitor, fullscreen):
        win = self.preview_window
        if win is None or not win.winfo_exists():
            return
        # Give the window a real geometry on the target screen first, so there
        # is something sane to fall back to when fullscreen is switched off
        win.geometry(self._guest_geometry_on(monitor, self.ext_scale))
        win.update_idletasks()
        if fullscreen:
            self._set_guest_fullscreen(True, monitor)
        win.after(120, self._sync_guest_layout)

    def _apply_guest_rect(self, monitor):
        """Put the borderless guest window exactly over `monitor`."""
        win = self.preview_window
        if win is None or not win.winfo_exists() or monitor is None or not self._guest_full:
            return
        # Raw Tk geometry: CTk's geometry() would scale the WxH half by the
        # window's DPI factor, and this rectangle is already in real pixels.
        tkinter.Wm.wm_geometry(
            win, f"{monitor['w']}x{monitor['h']}+{monitor['x']}+{monitor['y']}")

    def _guest_screen_now(self):
        """The monitor the guest window is currently sitting on."""
        win = self.preview_window
        if win is None or not win.winfo_exists():
            return None
        x, y = win.winfo_rootx(), win.winfo_rooty()
        for m in list_monitors():
            if m["x"] <= x < m["x"] + m["w"] and m["y"] <= y < m["y"] + m["h"]:
                return m
        return None

    def _set_guest_fullscreen(self, flag, monitor=None):
        """
        Fill a monitor with the guest window, borderless.

        Tk's own -fullscreen is unusable on a multi-screen Windows box: it sizes
        the window from winfo_screenwidth(), which reports the PRIMARY screen at
        the DPI Tk cached before the process became DPI aware — here a 1920x1200
        panel is reported as 1536x960. Pressing F11 on the projector therefore
        produced an undersized window dumped back on the laptop. Placing a
        borderless window on the monitor's exact pixel rectangle is precise and
        actually lands on the screen the guests are looking at.
        """
        win = self.preview_window
        if win is None or not win.winfo_exists():
            return
        self._guest_full = bool(flag)          # _apply_guest_rect checks this
        if flag:
            mon = monitor or self._guest_screen_now()
            if mon is None:                    # no monitor info — Tk's way is all we have
                try:
                    win.attributes("-fullscreen", True)
                except Exception as e:
                    print(f"Could not set guest fullscreen: {e}")
                    self._guest_full = False
                    return
            else:
                win.overrideredirect(True)
                self._apply_guest_rect(mon)
                # A freshly created CTkToplevel does deferred setup of its own
                # (titlebar colour, a geometry re-apply) which nudges the window
                # off the mark; re-assert the rectangle once that has landed.
                win.after(300, lambda: self._apply_guest_rect(mon))
                win.lift()
                # A borderless window isn't given focus, and without focus the
                # Esc/F11 bindings on it would never fire
                win.focus_force()
        else:
            win.overrideredirect(False)
            try:
                win.attributes("-fullscreen", False)
            except Exception:
                pass
            win.geometry(self._guest_geometry_on(monitor or self._guest_screen_now(),
                                                 self.ext_scale))

    def _toggle_guest_fullscreen(self, flag=None):
        win = self.preview_window
        if win is None or not win.winfo_exists():
            return
        if flag is None:
            flag = not self._guest_full
        self._set_guest_fullscreen(flag)
        # The stage only settles a frame or two after the mode change
        win.after(150, self._sync_guest_layout)

    def move_guest_to_next_screen(self):
        """Send the guest display to the next monitor, fullscreen, and keep it there."""
        screens = list_monitors()
        if len(screens) < 2:
            self.log_status("Only one screen detected — guest display stays here.")
            self._toggle_guest_fullscreen(True)
            return
        win = self.preview_window
        # Which screen is it on now? Compare against its own top-left corner.
        wx, wy = win.winfo_rootx(), win.winfo_rooty()
        current = 0
        for i, m in enumerate(screens):
            if m["x"] <= wx < m["x"] + m["w"] and m["y"] <= wy < m["y"] + m["h"]:
                current = i
                break
        target = screens[(current + 1) % len(screens)]
        self._place_guest_window(target, True)
        self.log_status(f"Guest display moved to screen {screens.index(target) + 1} "
                        f"({target['w']}x{target['h']}).")

    def open_preview_window(self):
        if self.preview_window is None or not self.preview_window.winfo_exists():
            monitor, fullscreen = self._guest_target()
            win = ctk.CTkToplevel(self)
            self.preview_window = win
            win.title("Photobooth — Guest Display")
            win.configure(fg_color=BG)
            # Size it on the target screen straight away. A toplevel left to be
            # sized by its own contents starts at a couple of hundred pixels,
            # which both looks broken and makes fullscreen fail.
            win.geometry(self._guest_geometry_on(monitor, self._scaling_of(win)))
            win.minsize(480, 360)
            # F11 toggles fullscreen for event use; Esc exits it
            win.bind("<F11>", lambda e: self._toggle_guest_fullscreen())
            win.bind("<Escape>", lambda e: self._toggle_guest_fullscreen(False))

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

            # Fonts are held rather than thrown away, so the guest text can be
            # re-sized for whatever screen the window ends up on
            self._ext_fonts = {
                "strip": _display_font(15),
                "count": _display_font(200),
                "instruction": _ui_font(20, "bold"),
                "qr": _ui_font(15, "bold"),
            }

            # Live layout preview down the right-hand side of the guest screen
            self.ext_strip_card, strip_col = _comic_card(self.ext_stage_frame,
                                                        border=3, keyline=0)
            self.ext_strip_card.place(relx=1.0, rely=0.5, anchor="e")
            self.ext_strip_title = ctk.CTkLabel(strip_col, text=_tracked("YOUR STRIP"),
                                                font=self._ext_fonts["strip"], text_color=ACCENT)
            self.ext_strip_title.pack(pady=(8, 5))
            self.ext_strip_label = ctk.CTkLabel(strip_col, text="")
            self.ext_strip_label.pack(padx=8, pady=(0, 8), expand=True)

            # Mascot animation, standing on the floor of the stage
            if self.brand.mascot_clip():
                self.ext_mascot_label = ctk.CTkLabel(self.ext_stage_frame, text="")
                self.ext_mascot_label.place(x=self._mascot_x, rely=1.0, y=-2, anchor="sw")

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
                                                    font=self._ext_fonts["count"],
                                                    text_color=ACCENT)
            self.ext_countdown_label.place(relx=0.5, rely=0.5, anchor="center")

            # Instruction line
            self.ext_instruction_label = ctk.CTkLabel(
                win, text="Get your props ready!  The operator will start the session soon.",
                font=self._ext_fonts["instruction"], text_color=TEXT)
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
                                               font=self._ext_fonts["qr"], text_color="#111111")
            self.ext_qr_caption.pack(padx=14, pady=(0, 14))
            # Stays hidden until show_qr_on_external() places it

            # Force the strip and the bands to redraw into the new widgets
            self._strip_sig = None
            self._ext_header_w = 0
            self._ext_footer_w = 0
            self._ext_stage_key = None
            self._ext_applied = (0.0, 0.0)
            self._start_mascot()
            self._place_guest_window(monitor, fullscreen)
            if monitor is not None:
                self.log_status(f"Guest display on {monitor['w']}x{monitor['h']} screen"
                                f"{' (fullscreen)' if fullscreen else ''}.")
        else:
            # Already open — the useful thing now is to send it to the projector
            self.move_guest_to_next_screen()
            self.preview_window.focus()

    # ---- Branded bands ---------------------------------------------------
    # Header and footer are rendered as images so they can use the real
    # artwork and the event font, then redrawn when the window width changes.

    def _band_image(self, label, width_px, height, builder, last_attr):
        # The band's own window decides the DPI factor — the guest display is
        # regularly on a screen that scales differently from the console.
        scale = self._scaling_of(label)
        w = int(width_px / scale)
        # Redraw on any height change (the guest bands scale with the screen),
        # but ignore the pixel-level width jitter of an ordinary resize.
        last_w, last_h = getattr(self, last_attr) or (0, 0)
        if w < 200 or (abs(w - last_w) < 12 and height == last_h):
            return
        setattr(self, last_attr, (w, height))
        try:
            img = builder((round(w * scale), round(height * scale)))
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
            self.ext_header_label, width_px, self._ext_header_h,
            lambda size: self.brand.header_band(size, align="center", mascot=False),
            "_ext_header_w")

    def _render_ext_footer(self, width_px):
        self._band_image(
            self.ext_footer_label, width_px, self._ext_footer_h,
            lambda size: self.brand.paper_band(
                size,
                ["JUNIOR INFORMATION TECHNOLOGY SOCIETY   ·   "
                 "COLLEGE OF INFORMATION TECHNOLOGY EDUCATION",
                 "URDANETA CITY UNIVERSITY   ·   AY 2026-2027"],
                text_color=_rgb(CITE_GRN), logos=("jits", "cite", "ucu")),
            "_ext_footer_w")

    def _fit_box(self, label, aspect_ratio, fallback, chrome=8):
        """Largest width/height (design units) with `aspect_ratio` that fits `label`."""
        scale = self._scaling_of(label)
        avail_w = int(label.winfo_width() / scale) - chrome
        avail_h = int(label.winfo_height() / scale) - chrome
        if avail_w < 80 or avail_h < 80:      # before the first layout pass
            avail_w, avail_h = fallback
        w = max(60, min(avail_w, int(avail_h * aspect_ratio)))
        return w, max(60, int(w / aspect_ratio))

    def update_video_stream(self):
        if self._closing:
            return
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
            op_scale = self.ui_scale
            frame_resized = cv2.resize(frame_rgb, (round(new_w * op_scale),
                                                   round(new_h * op_scale)))

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
                ext_scale = self.ext_scale
                ext_w, ext_h = self._ext_box_for(self.print_aspect)
                img_ext = Image.fromarray(_crop_fill(frame_rgb,
                                                    round(ext_w * ext_scale),
                                                    round(ext_h * ext_scale)))
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

    def _apply_ext_chrome(self, k, scale):
        """
        Re-size everything on the guest display that isn't the stage itself:
        the bands, the type and the comic frames.

        pack_configure() takes raw pixels — unlike CTk's own pack(), which
        scales what it's given — so the DPI factor is folded in by hand here.
        """
        win = self.preview_window
        if win is None or not win.winfo_exists():
            return
        raw = lambda v: max(1, round(v * k * scale))

        self._ext_header_h = max(52, round(EXT_HEADER_H * k))
        self._ext_footer_h = max(38, round(FOOTER_H * k))
        self.ext_header.configure(height=self._ext_header_h)
        self.ext_footer.configure(height=self._ext_footer_h)

        self._ext_fonts["instruction"].configure(size=max(11, round(20 * k)))
        self._ext_fonts["strip"].configure(size=max(9, round(15 * k)))
        self._ext_fonts["count"].configure(size=max(40, round(200 * k)))
        self._ext_fonts["qr"].configure(size=max(10, round(15 * k)))

        self.ext_instruction_label.pack_configure(pady=(0, raw(8)))
        self.ext_stage_frame.pack_configure(padx=raw(22), pady=(raw(10), raw(6)))
        _scale_comic_card(self.ext_video_card, k * scale)
        _scale_comic_card(self.ext_strip_card, k * scale)

        # The bands are images: force them to be rebuilt at the new height
        self._ext_header_w = 0
        self._ext_footer_w = 0
        self._render_ext_header(self.ext_header.winfo_width())
        self._render_ext_footer(self.ext_footer.winfo_width())

    def _sync_guest_layout(self):
        """Bring the guest display in line with the screen it's currently on."""
        win = self.preview_window
        if win is None or not win.winfo_exists():
            return
        scale, k = self._ext_metrics()
        last_scale, last_k = self._ext_applied
        if abs(k - last_k) > 0.02 or abs(scale - last_scale) > 0.01:
            self._ext_applied = (scale, k)
            self._ext_k = k
            self._apply_ext_chrome(k, scale)
        self._layout_ext_stage()

    def _watch_guest_display(self):
        """
        Poll the guest window for a change of screen.

        A <Configure> event alone isn't enough: dragging the window to a monitor
        of the same resolution but a different Windows scaling factor changes
        every design-unit size in the layout without moving a single pixel, and
        fires no resize event at all.
        """
        if self._closing:
            return
        try:
            self._sync_guest_layout()
        except Exception as e:
            print(f"Guest layout sync failed: {e}")
        self.after(400, self._watch_guest_display)

    def _solve_ext_stage(self, w, h, k):
        """
        Divide the stage width between the mascot, the photo frame, the QR card
        and the strip.

        The photo frame is the thing guests are actually looking at, so it is
        the one element with a floor: the strip is trimmed first, then the
        mascot is shrunk and finally dropped, before the frame gives up any
        room. That ordering is what keeps a 4:3 projector or a narrow window
        from pushing the picture off the stage.

        The QR column only exists while a QR is actually on screen, so the live
        feed keeps the whole middle for itself the rest of the time.
        """
        gap = max(6, round(12 * k))
        pad = max(6, round(14 * k))
        edge = max(8, round(20 * k))          # the strip card's own inner chrome
        aspect = (self.processor.canvas_width / self.processor.canvas_height
                  if self.processor.canvas_height else 1 / 3)

        # The strip is sized by the stage, not by its own card — the card wraps
        # its image, so reading its height back would just re-affirm itself.
        strip_h = max(140, h - max(30, round(52 * k)))   # heading, padding, border
        strip_w = round(strip_h * aspect) + edge
        mascot_w = round(MASCOT_MAX_W * k) if self.ext_mascot_label is not None else 0

        # Square QR plus the white card's padding and its caption line
        qr_side = qr_w = 0
        if self._ext_qr_showing:
            qr_pad = max(8, round(QR_CARD_PAD * k))
            qr_side = max(QR_MIN_SIDE, min(round(QR_SIDE * k),
                                           int(w * QR_MAX_W_FRAC) - 2 * qr_pad,
                                           h - 2 * pad - round(46 * k)))
            qr_w = qr_side + 2 * qr_pad

        def span(mw, sw, qw):
            """(left, right) design-unit bounds of the free middle."""
            lo = pad + mw + (gap if mw else 0)
            return lo, w - sw - gap - (qw + gap if qw else 0)

        floor = max(200, int(w * EXT_VIDEO_MIN_FRAC))

        # 1. Trim the strip — it stays perfectly readable well under full height
        lo, hi = span(mascot_w, strip_w, qr_w)
        if hi - lo < floor:
            spare = w - gap - lo - floor - edge - (qr_w + gap if qr_w else 0)
            strip_h = max(min(strip_h, int(spare / max(aspect, 0.05))),
                          max(120, int(h * 0.45)))
            strip_w = round(strip_h * aspect) + edge

        # 2. Shrink the mascot, then drop it rather than leave a sliver
        lo, hi = span(mascot_w, strip_w, qr_w)
        if mascot_w and hi - lo < floor:
            mascot_w = max(0, mascot_w - (floor - (hi - lo)))
            if mascot_w < EXT_MASCOT_MIN_W * k:
                mascot_w = 0

        # 3. Last resort — a QR too big for what is left shrinks rather than
        #    squeezing the photo below its floor
        lo, hi = span(mascot_w, strip_w, qr_w)
        if qr_w and hi - lo < floor:
            qr_side = max(QR_MIN_SIDE, qr_side - (floor - (hi - lo)))
            qr_w = qr_side + 2 * max(8, round(QR_CARD_PAD * k))

        lo, hi = span(mascot_w, strip_w, qr_w)
        return {
            "gap": gap, "pad": pad, "mascot_w": mascot_w,
            "strip_h": strip_h, "strip_w": strip_w,
            "qr_side": qr_side, "qr_w": qr_w,
            # Left edge of the QR card: hard against the strip, so the photo
            # keeps everything to its left
            "qr_x": w - strip_w - gap - qr_w, "qr_cy": h // 2,
            # Never negative, however cramped the stage gets
            "video_w": max(120, hi - lo), "video_h": max(90, h - 2 * pad),
            "video_cx": max(60, (lo + hi) // 2), "video_cy": h // 2,
        }

    def _layout_ext_stage(self, width_px=None, height_px=None):
        """
        Recompute the guest stage: how big the feed can be, where the photo
        frame sits between the mascot and the strip, and the background.

        Called on resize and from the screen watchdog, so it re-derives the DPI
        factor every time rather than trusting one read from startup.
        """
        if self.ext_stage_frame is None or not self.ext_stage_frame.winfo_exists():
            return
        scale, k = self._ext_metrics()
        if width_px is None:
            width_px = self.ext_stage_frame.winfo_width()
            height_px = self.ext_stage_frame.winfo_height()
        w = int(width_px / scale)
        h = int(height_px / scale)
        if w < 160 or h < 120:
            return
        # Key on the DPI factor too: moving to a same-sized screen that scales
        # differently changes every design-unit size without moving a pixel.
        # The QR flag is in here so showing/hiding it re-divides the stage.
        key = (w, h, round(k, 3), round(scale, 3), self._ext_qr_showing)
        if key == self._ext_stage_key:
            return
        self._ext_stage_key = key
        self._ext_stage_size = (w, h)
        self._ext_k = k

        plan = self._solve_ext_stage(w, h, k)
        self._ext_plan = plan
        self._strip_h_ext = plan["strip_h"]
        self._ext_strip_w = plan["strip_w"]
        self._strip_sig = None

        # Two traps here: Tk ADDS relx/rely to x/y, so the constructor's
        # relx=0.5 must be cleared, and CTk scales place() but not
        # place_configure() — so this has to go through place() to land in
        # design units.
        self.ext_video_card.place(relx=0, rely=0, x=plan["video_cx"],
                                  y=plan["video_cy"], anchor="center")
        self.ext_strip_card.place(relx=1.0, rely=0.5, anchor="e")

        # The QR card owns the slot between the photo and the strip
        if self._ext_qr_showing:
            self.ext_qr_frame.place(in_=self.ext_stage_frame, relx=0, rely=0,
                                    x=plan["qr_x"], y=plan["qr_cy"], anchor="w")
        else:
            self.ext_qr_frame.place_forget()

        if self.ext_mascot_label is not None:
            if plan["mascot_w"]:
                self._mascot_x = max(2, plan["pad"] // 2)
                self.ext_mascot_label.place(relx=0, rely=1.0, x=self._mascot_x,
                                            y=-2, anchor="sw")
            else:                              # no room for it on this screen
                self.ext_mascot_label.place_forget()

        self._mascot_display = None            # force the mascot to re-scale
        self._render_stage_bg()

        # A photo or QR already on screen was fitted to the old stage
        if self.showing_result and self._ext_result_path:
            self.show_result_on_external(self._ext_result_path, keep_timer=True)
            if self._ext_qr_url:
                self.show_qr_on_external(self._ext_qr_url, keep_timer=True)

    def _ext_box_for(self, aspect):
        """Largest box of `aspect` that fits the free middle of the guest stage."""
        plan = self._ext_plan
        chrome = self._ext_card_chrome(self._ext_k)
        if plan is None:                       # before the first layout pass
            w, h = self._ext_stage_size
            avail_w, avail_h = max(200, w // 2), max(150, h - 40)
        else:
            avail_w = max(100, plan["video_w"] - chrome)
            avail_h = max(80, plan["video_h"] - chrome)
        bw = max(100, min(avail_w, int(avail_h * aspect)))
        return bw, max(80, round(bw / aspect))

    def _ext_card_chrome(self, k):
        """Design units the comic frame and its padding add around the picture."""
        return 2 * (max(1, round(CARD_BORDER * k)) + max(1, round(CARD_KEYLINE * k)) + 6)

    def _render_stage_bg(self):
        """
        The swirl backdrop behind the stage. Drawn once per size — turning a
        stage-sized image into a Tk PhotoImage costs ~60ms, far too much to do
        on an animation tick, so the moving parts are separate small labels.
        """
        if self.ext_bg_label is None or not self.ext_bg_label.winfo_exists():
            return
        w, h = self._ext_stage_size
        scale, k = self.ext_scale, self._ext_k
        px = lambda v: max(1, round(v * scale))

        base = self.brand.backdrop((px(w), px(h)), dim=0.13, vignette=0.9)
        img = (base.convert("RGBA") if base is not None
               else Image.new("RGBA", (px(w), px(h)), _rgb(BG) + (255,)))

        # Comic stickers composited straight in: static, so they cost nothing
        # per frame, and the mascot picks them up in its background patch.
        for asset, fx, fy, sw, _phase in EXT_STICKERS:
            art = self.brand.sticker(asset, px(sw * k))
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
        """
        Mascot size in design units: the width the stage solver actually left
        for it, then capped by the stage height.
        """
        _, stage_h = self._ext_stage_size
        budget = self._ext_plan["mascot_w"] if self._ext_plan else round(MASCOT_MAX_W * self._ext_k)
        mh = round(max(40, budget - 6) * src.height / src.width)
        if stage_h:
            mh = min(mh, stage_h - max(6, round(10 * self._ext_k)))
        mh = max(80, mh)
        return max(1, round(src.width * mh / src.height)), mh

    def _tick_mascot(self):
        frames = self._mascot_frames
        label = self.ext_mascot_label
        if self._closing or not frames or label is None or not label.winfo_exists():
            self._mascot_job = None
            return
        if self._ext_plan is not None and not self._ext_plan["mascot_w"]:
            # Hidden on this screen — keep the loop alive in case it comes back
            self._mascot_job = self.after(self._mascot_interval, self._tick_mascot)
            return

        scale = self.ext_scale
        mw, mh = self._mascot_size(frames[0])
        phys = (max(1, round(mw * scale)), max(1, round(mh * scale)))
        if self._mascot_display is None or self._mascot_phys != phys:
            self._mascot_phys = phys
            self._mascot_display = [f.resize(phys, Image.Resampling.BILINEAR)
                                    for f in frames]
            self._mascot_patch = None

        # The mascot is keyed, so it needs the stage texture behind it to blend
        if self._mascot_patch is None:
            self._mascot_patch = self._stage_patch_at(self._mascot_x, mh, phys)

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
        scale = self.ext_scale
        x0 = round(x * scale)
        y0 = self._stage_bg.height - round(2 * scale) - phys[1]
        if y0 < 0 or x0 + phys[0] > self._stage_bg.width:
            return None
        return self._stage_bg.crop((x0, y0, x0 + phys[0], y0 + phys[1]))

    def _on_strip_resize(self, panel_height, which):
        """Keep the layout preview filling its panel as windows resize / go fullscreen."""
        # heading + padding + the comic frame's border and keyline
        chrome = (62 if which == "op" else 46) + 6
        scale = self.ui_scale if which == "op" else self.ext_scale
        h = max(220, int(panel_height / scale) - chrome)
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

        # The console's strip column is sized by this image, so a wide template
        # would otherwise eat the room the camera and controls need. Height is
        # the free axis: trim it until the width is back within its share.
        op_h = self._strip_h_op
        op_max_w = max(200, round(self.winfo_width() / self.ui_scale * 0.28))
        op_h = max(200, min(op_h, round(op_max_w / max(aspect, 0.05))))
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

    def show_result_on_external(self, filepath, keep_timer=False):
        # Show the finished composite on the guest-facing display
        if not (self.preview_window and self.preview_window.winfo_exists()):
            return
        try:
            # A QR from the previous session still owns a column — take it back
            # before measuring, or this photo gets fitted to the narrower stage
            if not keep_timer and self._ext_qr_showing:
                self._ext_qr_url = None
                self._ext_qr_showing = False
                self.ext_qr_frame.place_forget()
                self._relayout_ext_stage()

            img = Image.open(filepath)
            # Fit the portrait composite within the external video area
            size = self._ext_box_for(img.width / img.height)
            imgtk = ctk.CTkImage(light_image=img, dark_image=img, size=size)
            self.showing_result = True
            # Remembered so the photo can be re-fitted if the guest window is
            # moved or fullscreened while it's still up
            self._ext_result_path = filepath
            if not keep_timer:
                self._ext_qr_url = None
            self.ext_video_label.configure(image=imgtk, text="")
            self.ext_video_label.image = imgtk
            self.ext_instruction_label.configure(text="Here's your photo! 📸", text_color=TEXT)
            if not keep_timer:
                self._schedule_ext_resume()
        except Exception as e:
            self.log_status(f"External preview error: {e}")

    def show_qr_on_external(self, url, keep_timer=False):
        # A QR of the photo's public URL, in its own card beside the picture so
        # guests can scan it without the photo being hidden. Only meaningful
        # while the result photo is still on screen.
        if not (self.preview_window and self.preview_window.winfo_exists() and self.showing_result):
            return
        try:
            target = self.gdrive_folder_url or url
            self._ext_qr_url = url
            # Claim the QR column and re-divide the stage around it. The photo
            # gives up that width, and the re-layout re-fits it to what's left.
            was_showing = self._ext_qr_showing
            self._ext_qr_showing = True
            if not was_showing:
                self._relayout_ext_stage()

            plan = self._ext_plan
            side = max(QR_MIN_SIDE, plan["qr_side"] if plan and plan["qr_side"]
                       else round(QR_SIDE * self._ext_k))
            scale = self.ext_scale
            qr_img = self._make_qr_image(target, size=max(1, round(side * scale)))
            qrtk = ctk.CTkImage(light_image=qr_img, dark_image=qr_img, size=(side, side))
            self.ext_qr_label.configure(image=qrtk)
            self.ext_qr_label.image = qrtk
            self.ext_qr_caption.configure(
                text="Scan for the photo folder" if self.gdrive_folder_url
                else "Scan to download")
            # Its own slot between the photo and the strip, so the picture guests
            # just took stays whole behind it
            if plan is not None:
                self.ext_qr_frame.place(in_=self.ext_stage_frame, relx=0, rely=0,
                                        x=plan["qr_x"], y=plan["qr_cy"], anchor="w")
            else:
                self.ext_qr_frame.place(relx=0.82, rely=0.5, anchor="center")
            self.ext_instruction_label.configure(
                text="Scan the QR code to get your photos!", text_color=TEXT)
            # Reset the countdown so the full window is measured from when the QR
            # appears, and give it the QR's longer duration rather than the photo's
            if not keep_timer:
                self._schedule_ext_resume(self.qr_display_ms)
        except Exception as e:
            # Don't leave the stage holding a column for a QR that never landed
            self._ext_qr_showing = False
            self._relayout_ext_stage()
            self.log_status(f"QR code error: {e}")

    def _relayout_ext_stage(self):
        """
        Re-divide the guest stage right now. Only needed when something other
        than a resize changed the division — the QR column appearing or going
        away — since the cached key covers the geometry cases on its own.
        """
        if self.preview_window and self.preview_window.winfo_exists():
            self._layout_ext_stage()

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

    def _schedule_ext_resume(self, delay_ms=None):
        """Hand the guest display back to the live feed after `delay_ms`.

        A delay of 0 cancels the handover entirely — whatever is on screen stays
        there until the operator starts the next session.
        """
        if self._ext_resume_id is not None:
            self.after_cancel(self._ext_resume_id)
            self._ext_resume_id = None
        if delay_ms is None:
            delay_ms = self.result_display_ms
        if delay_ms <= 0:
            return
        self._ext_resume_id = self.after(delay_ms, self.resume_external_live)

    def _clear_external_result(self):
        # Stop showing the result photo/QR and cancel any pending auto-resume timer
        self.showing_result = False
        self._ext_result_path = None
        self._ext_qr_url = None
        if self._ext_resume_id is not None:
            self.after_cancel(self._ext_resume_id)
            self._ext_resume_id = None
        if self.preview_window and self.preview_window.winfo_exists():
            self.ext_qr_frame.place_forget()
            # Give the column back to the live feed
            if self._ext_qr_showing:
                self._ext_qr_showing = False
                self._relayout_ext_stage()

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
        # Stops the polling loops rescheduling themselves onto a dead
        # interpreter, which Tk reports as "invalid command name" on the way out
        self._closing = True
        self.camera.stop()
        self.destroy()
