import os
import json
import shutil
import boto3
from botocore.exceptions import ClientError
from pathlib import Path
from dotenv import load_dotenv
from datetime import datetime

class Uploader:
    def __init__(self):
        load_dotenv()
        self.account_id = os.environ.get("R2_ACCOUNT_ID")
        self.access_key = os.environ.get("R2_ACCESS_KEY_ID")
        self.secret_key = os.environ.get("R2_SECRET_ACCESS_KEY")
        self.bucket_name = os.environ.get("R2_BUCKET_NAME")
        self.public_url_base = os.environ.get("R2_PUBLIC_URL", "").rstrip('/')
        self.gdrive_dir = os.environ.get("GOOGLE_DRIVE_FINAL_DIR")
        
        self.pending_file = "pending_uploads.json"
        
        # Initialize boto3 client if credentials exist
        self.s3 = None
        if self.account_id and self.access_key and self.secret_key:
            endpoint_url = f"https://{self.account_id}.r2.cloudflarestorage.com"
            self.s3 = boto3.client(
                's3',
                endpoint_url=endpoint_url,
                aws_access_key_id=self.access_key,
                aws_secret_access_key=self.secret_key,
                region_name='auto'
            )

    def copy_to_gdrive(self, local_filepath):
        if not self.gdrive_dir:
            return False, "Google Drive directory not configured."
        
        try:
            Path(self.gdrive_dir).mkdir(parents=True, exist_ok=True)
            filename = os.path.basename(local_filepath)
            dest_path = os.path.join(self.gdrive_dir, filename)
            shutil.copy2(local_filepath, dest_path)
            return True, dest_path
        except Exception as e:
            return False, str(e)

    def upload_to_r2(self, local_filepath):
        if not self.s3 or not self.bucket_name:
            self._add_to_pending(local_filepath)
            return False, "R2 credentials not configured. Added to pending."
        
        try:
            filename = os.path.basename(local_filepath)
            date_folder = datetime.now().strftime("%Y-%m-%d")
            object_key = f"photobooth/{date_folder}/{filename}"
            
            self.s3.upload_file(
                local_filepath,
                self.bucket_name,
                object_key,
                ExtraArgs={'ContentType': 'image/jpeg'}
            )
            
            public_url = f"{self.public_url_base}/{object_key}"
            return True, public_url
            
        except Exception as e:
            self._add_to_pending(local_filepath)
            return False, f"Upload failed: {str(e)}. Added to pending."

    def _add_to_pending(self, filepath):
        pending = self.get_pending_uploads()
        if filepath not in pending:
            pending.append(filepath)
            self._save_pending(pending)

    def _remove_from_pending(self, filepath):
        pending = self.get_pending_uploads()
        if filepath in pending:
            pending.remove(filepath)
            self._save_pending(pending)

    def get_pending_uploads(self):
        if os.path.exists(self.pending_file):
            try:
                with open(self.pending_file, 'r') as f:
                    return json.load(f)
            except:
                return []
        return []

    def _save_pending(self, pending_list):
        with open(self.pending_file, 'w') as f:
            json.dump(pending_list, f)

    def retry_pending_uploads(self):
        pending = self.get_pending_uploads()
        results = []
        for filepath in pending.copy():
            if not os.path.exists(filepath):
                self._remove_from_pending(filepath)
                continue
                
            success, msg = self.upload_to_r2(filepath)
            if success:
                self._remove_from_pending(filepath)
            results.append((filepath, success, msg))
        return results
