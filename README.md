# School Photobooth Automation

A Python GUI photobooth app designed for school organizations.

## Features
- CustomTkinter GUI with dark mode.
- OpenCV live webcam preview and 4-photo capture.
- Automated layout generation using a customizable strip template.
- Automatically copies final photos to a Google Drive folder.
- Automatically uploads final photos to Cloudflare R2 object storage.
- Auto-retries and pending upload queue for offline scenarios.

## Setup

1. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Configure environment:**
   Copy `.env.example` to `.env` and fill in your Cloudflare R2 and Google Drive details.

3. **Configure app settings:**
   Edit `config.json` if you need to adjust camera index, countdown seconds, or photo slot coordinates.

## Guest display (second screen / projector)

The guest-facing window sizes itself from the screen it is actually on — its
resolution, its aspect and its own Windows scaling factor — so it looks the
same on a laptop panel, a 1080p projector and a 4K TV.

- With a second screen attached it opens there, filling it, and the operator
  console stays on the laptop.
- **F11** toggles fullscreen, **Esc** leaves it. Both work from either window,
  so a borderless guest display can always be dismissed.
- The **Guest Display → Next Screen** button moves it to the next monitor and
  fills that one — use it when the projector is plugged in after the app started.

Two optional `config.json` keys override the automatic behaviour:

| Key | Default | Meaning |
| --- | --- | --- |
| `guest_monitor` | `"auto"` | `"auto"` picks the second screen when there is one. Set a number (`0` = primary, `1` = second, …) to pin it. |
| `guest_fullscreen` | `"auto"` | `"auto"` fills the screen only when a second one is attached. `true` / `false` force it. |

### How long the result stays up

Once a session ends, the guest display shows the finished strip, then the QR
code appears beside it as soon as the upload lands. They have separate timers,
because a QR needs longer than a photo — guests have to get a phone out, unlock
it and open the camera before they can even aim.

| Key | Default | Meaning |
| --- | --- | --- |
| `result_display_seconds` | `12` | How long the finished strip stays up when no QR follows it. |
| `qr_display_seconds` | `30` | How long the QR stays, counted from when it appears. **`0` leaves it up until the operator starts the next session.** |

The QR gets its own card between the photo and the strip preview, so the picture
guests just took is never covered. The photo gives that width back the moment
the QR clears.

4. **Background Template:**
   The strip artwork is `assets/new_background.png`, a 1080x1920 image with the
   title lockup and the footer baked in. Photos are pasted *on top* of it at the
   `photo_slots` coordinates, so transparent cutouts aren't required.

   To swap in different artwork, set `background_asset` in `config.json` and
   update the layout that goes with it — `canvas_size`, `photo_slots`, the
   `overlays` sticker positions, and `lockup_box` (the rectangle the window
   header's title is cropped from). `python scratch_bg.py` renders a placeholder
   backdrop with the slot rectangles drawn on it if you need to check the fit.

5. **Run the App:**
   ```bash
   python main.py
   ```
