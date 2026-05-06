"""
Download all MuseTalk model weights.

Usage:
    python download_models.py            # download everything
    python download_models.py --check    # just verify sizes, no download
    python download_models.py --skip-syncnet  # skip optional SyncNet
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

MODELS_DIR = Path(__file__).parent / "models"


def _hf_download(repo_id: str, local_dir: Path, include: list[str]) -> None:
    """Download specific files from a Hugging Face repo."""
    local_dir.mkdir(parents=True, exist_ok=True)
    from huggingface_hub import hf_hub_download

    for pattern in include:
        dest = local_dir / pattern
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            print(f"  skip (exists): {dest.relative_to(MODELS_DIR.parent)}")
            continue
        print(f"  downloading {repo_id}/{pattern} ...")
        tmp = hf_hub_download(repo_id=repo_id, filename=pattern, local_dir=str(local_dir))
        # hf_hub_download may write to a cache sub-path; move to flat location
        tmp_path = Path(tmp)
        if tmp_path != dest:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(tmp_path), str(dest))
        print(f"  -> {dest.relative_to(MODELS_DIR.parent)}")


def _curl_download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        print(f"  skip (exists): {dest.relative_to(MODELS_DIR.parent)}")
        return
    print(f"  downloading {url} ...")
    subprocess.run(["curl", "-L", url, "-o", str(dest)], check=True)
    print(f"  -> {dest.relative_to(MODELS_DIR.parent)}")


def _gdown_download(file_id: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        print(f"  skip (exists): {dest.relative_to(MODELS_DIR.parent)}")
        return
    print(f"  downloading GDrive:{file_id} ...")
    try:
        import gdown
        gdown.download(id=file_id, output=str(dest), quiet=False)
    except ImportError:
        subprocess.run([sys.executable, "-m", "pip", "install", "gdown", "-q"], check=True)
        import gdown
        gdown.download(id=file_id, output=str(dest), quiet=False)
    print(f"  -> {dest.relative_to(MODELS_DIR.parent)}")


def download_all(skip_syncnet: bool = False) -> None:
    print("\n=== MuseTalk V1.5 UNet + config ===")
    _hf_download(
        "TMElyralab/MuseTalk",
        MODELS_DIR,
        include=[
            "musetalkV15/musetalk.json",
            "musetalkV15/unet.pth",
        ],
    )

    print("\n=== Stable Diffusion VAE (sd-vae-ft-mse) ===")
    _hf_download(
        "stabilityai/sd-vae-ft-mse",
        MODELS_DIR / "sd-vae",
        include=[
            "config.json",
            "diffusion_pytorch_model.bin",
        ],
    )

    print("\n=== Whisper-tiny ===")
    _hf_download(
        "openai/whisper-tiny",
        MODELS_DIR / "whisper",
        include=[
            "config.json",
            "pytorch_model.bin",
            "preprocessor_config.json",
        ],
    )

    print("\n=== DWPose (RTMPose-L) ===")
    (MODELS_DIR / "dwpose").mkdir(parents=True, exist_ok=True)
    _hf_download(
        "yzd-v/DWPose",
        MODELS_DIR / "dwpose",
        include=["dw-ll_ucoco_384.pth"],
    )

    print("\n=== Face-Parse-BiSeNet ===")
    _gdown_download(
        "154JgKpzCPW82qINcVieuPH3fZ2e0P812",
        MODELS_DIR / "face-parse-bisent" / "79999_iter.pth",
    )
    _curl_download(
        "https://download.pytorch.org/models/resnet18-5c106cde.pth",
        MODELS_DIR / "face-parse-bisent" / "resnet18-5c106cde.pth",
    )

    if not skip_syncnet:
        print("\n=== SyncNet (optional — used for eval only) ===")
        _hf_download(
            "ByteDance/LatentSync",
            MODELS_DIR / "syncnet",
            include=["latentsync_syncnet.pt"],
        )

    _print_summary()


def check_models() -> bool:
    required = [
        MODELS_DIR / "musetalkV15" / "unet.pth",
        MODELS_DIR / "musetalkV15" / "musetalk.json",
        MODELS_DIR / "sd-vae" / "config.json",
        MODELS_DIR / "sd-vae" / "diffusion_pytorch_model.bin",
        MODELS_DIR / "whisper" / "config.json",
        MODELS_DIR / "whisper" / "pytorch_model.bin",
        MODELS_DIR / "dwpose" / "dw-ll_ucoco_384.pth",
        MODELS_DIR / "face-parse-bisent" / "79999_iter.pth",
        MODELS_DIR / "face-parse-bisent" / "resnet18-5c106cde.pth",
    ]
    ok = True
    for p in required:
        status = "OK" if p.exists() else "MISSING"
        size = f"{p.stat().st_size // 1024 // 1024} MB" if p.exists() else ""
        print(f"  [{status}] {p.relative_to(MODELS_DIR.parent)}  {size}")
        if not p.exists():
            ok = False
    return ok


def _print_summary() -> None:
    if MODELS_DIR.exists():
        total = sum(f.stat().st_size for f in MODELS_DIR.rglob("*") if f.is_file())
        print(f"\nModels total size: {total / 1024 / 1024:.0f} MB")
    print("\n✅ Done. Run `python download_models.py --check` to verify.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download MuseTalk model weights")
    parser.add_argument("--check", action="store_true", help="Verify weights exist without downloading")
    parser.add_argument("--skip-syncnet", action="store_true", help="Skip optional SyncNet weights")
    args = parser.parse_args()

    os.chdir(Path(__file__).parent)

    if args.check:
        ok = check_models()
        sys.exit(0 if ok else 1)
    else:
        download_all(skip_syncnet=args.skip_syncnet)
