"""
Celery tasks for storage operations (image download, extraction).
Includes download progress tracking.
"""

import hashlib
import lzma
import os
import tarfile
import urllib.request
import uuid
from pathlib import Path

from app.workers.celery_app import celery_app
from app.core.config import settings
from app.services.image_validation import inspect_cloud_image_sync, reject_arm_filename


def _get_sync_session():
    """Create a synchronous database session for Celery tasks."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    sync_url = settings.DATABASE_URL.replace("+asyncpg", "+psycopg2")
    if "postgresql://" in sync_url and "+psycopg2" not in sync_url:
        sync_url = sync_url.replace("postgresql://", "postgresql+psycopg2://")
    engine = create_engine(sync_url)
    return Session(engine)


def _download_with_progress(url: str, dest_path: str, session, image_id: str):
    """Download a file with progress tracking via DB updates."""
    from app.models.storage import CloudImage

    req = urllib.request.Request(url, headers={"User-Agent": "QEMU-Interface/1.0"})
    response = urllib.request.urlopen(req, timeout=600)

    total_size = int(response.headers.get("Content-Length", 0))
    downloaded = 0
    last_progress = -1
    chunk_size = 1024 * 1024  # 1 MB chunks

    with open(dest_path, "wb") as f:
        while True:
            chunk = response.read(chunk_size)
            if not chunk:
                break
            f.write(chunk)
            downloaded += len(chunk)

            # Calculate progress
            if total_size > 0:
                progress = min(95, int((downloaded / total_size) * 95))
            else:
                # Unknown size: show incremental progress based on MB downloaded
                progress = min(90, downloaded // (10 * 1024 * 1024))

            # Update DB every 5% change
            if progress != last_progress and (progress - last_progress >= 5 or progress >= 95):
                try:
                    image = session.get(CloudImage, uuid.UUID(image_id))
                    if image:
                        image.download_progress = progress
                    session.commit()
                    last_progress = progress
                except Exception:
                    session.rollback()

    return downloaded


@celery_app.task(bind=True, name="app.workers.storage_tasks.download_cloud_image", max_retries=2)
def download_cloud_image(self, image_id: str):
    """
    Download a cloud image from URL, extract if needed, compute checksum.
    Reports download progress to the database.

    Supports:
    - Direct .qcow2 / .img files
    - .tar.xz archives (extracts largest file)
    - .xz compressed files (decompresses)
    """
    from app.models.storage import CloudImage, ImageDownloadStatus, StoragePool

    session = _get_sync_session()
    try:
        image = session.get(CloudImage, uuid.UUID(image_id))
        if not image or not image.download_url:
            return {"error": "Image not found or no download URL"}

        # Update status: downloading
        image.download_status = ImageDownloadStatus.DOWNLOADING
        image.download_progress = 0
        image.download_error = None
        session.commit()

        # Determine storage path
        storage_path = settings.CLOUD_IMAGES_PATH
        if image.storage_pool_id:
            pool = session.get(StoragePool, image.storage_pool_id)
            if pool:
                storage_path = pool.path

        os.makedirs(storage_path, exist_ok=True)

        url = image.download_url
        filename = url.split("/")[-1].split("?")[0] or f"image-{image_id[:8]}"
        download_path = os.path.join(storage_path, f"download-{image_id[:8]}-{filename}")

        # Download file with progress tracking
        print(f"[Image {image_id[:8]}] Downloading from {url}...")
        file_size = _download_with_progress(url, download_path, session, image_id)
        print(f"[Image {image_id[:8]}] Downloaded {file_size / (1024*1024):.1f} MB")

        # Determine final file path
        final_path = download_path
        image.original_filename = filename

        # Handle .tar.xz archives
        if filename.endswith(".tar.xz") or filename.endswith(".tar.gz"):
            image.download_status = ImageDownloadStatus.EXTRACTING
            image.download_progress = 96
            session.commit()

            print(f"[Image {image_id[:8]}] Extracting archive...")
            extract_dir = os.path.join(storage_path, f"extract-{image_id[:8]}")
            os.makedirs(extract_dir, exist_ok=True)

            mode = "r:xz" if filename.endswith(".tar.xz") else "r:gz"
            with tarfile.open(download_path, mode) as tar:
                tar.extractall(extract_dir)

            # Find the largest file (likely the disk image)
            largest_file = None
            largest_size = 0
            for root, dirs, files in os.walk(extract_dir):
                for f in files:
                    fp = os.path.join(root, f)
                    sz = os.path.getsize(fp)
                    if sz > largest_size:
                        largest_size = sz
                        largest_file = fp

            if largest_file:
                ext = Path(largest_file).suffix or ".qcow2"
                arch_tag = (image.architecture or "x86_64").replace("/", "-")
                final_name = f"{image.os_type}-{image.os_version}-{arch_tag}-{image_id[:8]}{ext}"
                final_path = os.path.join(storage_path, final_name)
                os.rename(largest_file, final_path)

            # Clean up
            os.remove(download_path)
            _rmtree_safe(extract_dir)

        # Handle .xz compressed files (not tar)
        elif filename.endswith(".xz") and not filename.endswith(".tar.xz"):
            image.download_status = ImageDownloadStatus.EXTRACTING
            image.download_progress = 96
            session.commit()

            print(f"[Image {image_id[:8]}] Decompressing .xz...")
            decompressed_name = filename[:-3]  # Remove .xz
            ext = Path(decompressed_name).suffix or ".qcow2"
            arch_tag = (image.architecture or "x86_64").replace("/", "-")
            final_name = f"{image.os_type}-{image.os_version}-{arch_tag}-{image_id[:8]}{ext}"
            final_path = os.path.join(storage_path, final_name)

            with lzma.open(download_path, "rb") as xz_file:
                with open(final_path, "wb") as out_file:
                    while True:
                        chunk = xz_file.read(8 * 1024 * 1024)  # 8MB chunks
                        if not chunk:
                            break
                        out_file.write(chunk)

            os.remove(download_path)

        # Handle direct .qcow2 / .img / .raw files
        else:
            ext = Path(filename).suffix or ".qcow2"
            arch_tag = (image.architecture or "x86_64").replace("/", "-")
            final_name = f"{image.os_type}-{image.os_version}-{arch_tag}-{image_id[:8]}{ext}"
            final_path = os.path.join(storage_path, final_name)
            if download_path != final_path:
                os.rename(download_path, final_path)

        # Compute SHA256 checksum
        image.download_progress = 98
        session.commit()

        print(f"[Image {image_id[:8]}] Computing checksum...")
        sha256 = hashlib.sha256()
        with open(final_path, "rb") as f:
            while True:
                chunk = f.read(8 * 1024 * 1024)
                if not chunk:
                    break
                sha256.update(chunk)

        image_format = image.format.value if hasattr(image.format, "value") else str(image.format)
        if image_format != "iso":
            try:
                # Check original download name too (final name may be rewritten)
                reject_arm_filename(filename)
                reject_arm_filename(os.path.basename(final_path))
                image.architecture = inspect_cloud_image_sync(final_path)
            except ValueError as e:
                # Fall back when virt-inspector fails but URL/name clearly says amd64
                src = f"{filename} {image.download_url or ''}".lower()
                declared = (image.architecture or "").lower()
                if declared in ("x86_64", "amd64") and any(
                    m in src for m in ("amd64", "x86_64", "x86-64")
                ):
                    print(
                        f"[Image {image_id[:8]}] Inspect failed ({e}); "
                        "keeping declared x86_64 from amd64 download URL/name"
                    )
                    image.architecture = "x86_64"
                else:
                    if os.path.isfile(final_path):
                        os.remove(final_path)
                    raise RuntimeError(str(e)) from e

        # Update image record
        image.file_path = final_path
        image.file_size_bytes = os.path.getsize(final_path)
        image.checksum_sha256 = sha256.hexdigest()
        image.download_status = ImageDownloadStatus.READY
        image.download_progress = 100
        image.download_error = None
        session.commit()

        print(f"[Image {image_id[:8]}] Ready: {final_path} ({image.file_size_bytes / (1024*1024):.1f} MB, SHA256: {image.checksum_sha256[:16]}...)")

        return {
            "image_id": image_id,
            "file_path": final_path,
            "file_size_bytes": image.file_size_bytes,
            "checksum_sha256": image.checksum_sha256,
        }

    except Exception as e:
        print(f"[Image {image_id[:8]}] Download failed: {e}")
        try:
            image = session.get(CloudImage, uuid.UUID(image_id))
            if image:
                image.download_status = ImageDownloadStatus.FAILED
                image.download_error = str(e)[:2000]
                image.download_progress = 0
                session.commit()
        except Exception:
            session.rollback()
        raise

    finally:
        session.close()


def _rmtree_safe(path: str):
    """Safely remove a directory tree."""
    import shutil
    try:
        shutil.rmtree(path)
    except Exception:
        pass
