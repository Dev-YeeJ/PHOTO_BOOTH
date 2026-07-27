# School Photobooth Automation

A Python GUI photobooth app designed for school organizations.

## Features
- CustomTkinter GUI with dark mode.
- OpenCV live webcam preview and 4-photo capture.
- Automated layout generation using a customizable `background.png`.
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

4. **Background Template:**
   Ensure you have a 1200x1800 image at `assets/background.png`. It should have transparent cutouts where you want the photos to appear (though this code currently pastes photos *on top* of the background based on coordinates, so transparent cutouts aren't strictly required unless pasting underneath. We paste on top in the given slots).

5. **Run the App:**
   ```bash
   python main.py
   ```
