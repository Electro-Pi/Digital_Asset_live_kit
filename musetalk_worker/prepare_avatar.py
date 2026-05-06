"""
Bake a per-identity cache for MuseTalk real-time inference.

Usage:
    python prepare_avatar.py --name sample --source upstream/data/video/sun.mp4
    python prepare_avatar.py --name boss   --source /path/to/portrait.jpg --force
    python prepare_avatar.py --name custom --source video.mp4 --bbox-shift 5 --parsing-mode jaw

The output is written to avatars/<name>/ and contains:
  coords.pkl          per-frame (x1,y1,x2,y2) face bounding boxes (cycled)
  mask_coords.pkl     per-frame crop boxes for blending masks
  latents.pt          VAE-encoded 256x256 face crops as fp16 latents (cycled)
  full_imgs/          original frames written as 08d.png
  mask/               blending masks per frame as 08d.png
  meta.json           avatar metadata
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import pickle
import shutil
import sys
from pathlib import Path

# Make upstream musetalk importable
WORKER_DIR = Path(__file__).parent.resolve()
UPSTREAM_DIR = WORKER_DIR / "upstream"
sys.path.insert(0, str(UPSTREAM_DIR))

import cv2
import numpy as np
import torch
from tqdm import tqdm


def _video_to_imgs(vid_path: str, save_path: str) -> None:
    cap = cv2.VideoCapture(vid_path)
    count = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        cv2.imwrite(f"{save_path}/{count:08d}.png", frame)
        count += 1
    cap.release()


def _image_to_video_frames(img_path: str, save_path: str, n_frames: int = 250) -> None:
    """Replicate a single portrait image as n_frames video frames (10s @ 25fps)."""
    img = cv2.imread(img_path)
    if img is None:
        raise FileNotFoundError(f"Cannot read image: {img_path}")
    for i in range(n_frames):
        cv2.imwrite(f"{save_path}/{i:08d}.png", img)


def bake_avatar(
    name: str,
    source: str,
    bbox_shift: int = 0,
    extra_margin: int = 10,
    parsing_mode: str = "jaw",
    left_cheek_width: int = 90,
    right_cheek_width: int = 90,
    device: str = "cuda",
    force: bool = False,
) -> Path:
    """Bake a per-identity cache. Returns the avatar directory path."""
    avatar_dir = WORKER_DIR / "avatars" / name
    meta_path = avatar_dir / "meta.json"

    if meta_path.exists() and not force:
        print(f"Avatar '{name}' already exists at {avatar_dir}. Use --force to re-create.")
        return avatar_dir

    if avatar_dir.exists():
        shutil.rmtree(avatar_dir)

    full_imgs_path = avatar_dir / "full_imgs"
    mask_out_path = avatar_dir / "mask"
    full_imgs_path.mkdir(parents=True, exist_ok=True)
    mask_out_path.mkdir(parents=True, exist_ok=True)

    # ---------- Step 1: extract frames ----------
    src = Path(source)
    if not src.exists():
        raise FileNotFoundError(f"Source not found: {source}")

    ext = src.suffix.lower()
    if ext in {".jpg", ".jpeg", ".png", ".bmp"}:
        print(f"Source is an image — replicating as 250 frames (10s @ 25fps) ...")
        _image_to_video_frames(str(src), str(full_imgs_path))
    else:
        print(f"Extracting frames from {source} ...")
        _video_to_imgs(str(src), str(full_imgs_path))

    input_img_list = sorted(glob.glob(str(full_imgs_path / "*.[jpJP][pnPN]*[gG]")))
    if not input_img_list:
        raise RuntimeError(f"No frames extracted from {source}")
    print(f"  {len(input_img_list)} frames extracted")

    # ---------- Step 2: load models ----------
    print("Loading models ...")
    # Upstream MuseTalk resolves DWPose config/checkpoint as ./musetalk/... and ./models/dwpose/...
    # from the upstream root (see upstream/musetalk/utils/preprocessing.py). Weights live under
    # musetalk_worker/models/ — symlink upstream/models -> ../models in the repo.
    os.chdir(UPSTREAM_DIR)

    from musetalk.utils.utils import load_all_model
    from musetalk.utils.preprocessing import get_landmark_and_bbox, read_imgs
    from musetalk.utils.blending import get_image_prepare_material
    from musetalk.utils.face_parsing import FaceParsing

    torch_device = torch.device(device if torch.cuda.is_available() else "cpu")
    vae, unet, pe = load_all_model(
        unet_model_path=str(WORKER_DIR / "models" / "musetalkV15" / "unet.pth"),
        vae_type="sd-vae",
        unet_config=str(WORKER_DIR / "models" / "musetalkV15" / "musetalk.json"),
        device=torch_device,
    )
    vae.vae = vae.vae.half().to(torch_device)
    unet.model = unet.model.half().to(torch_device)
    pe = pe.half().to(torch_device)

    fp = FaceParsing(left_cheek_width=left_cheek_width, right_cheek_width=right_cheek_width)

    # ---------- Step 3: landmarks + VAE latents ----------
    print("Extracting landmarks and computing face latents ...")
    coord_list, frame_list = get_landmark_and_bbox(input_img_list, bbox_shift)

    input_latent_list = []
    coord_placeholder = (0.0, 0.0, 0.0, 0.0)
    for idx, (bbox, frame) in enumerate(zip(coord_list, frame_list)):
        if bbox == coord_placeholder:
            input_latent_list.append(None)
            continue
        x1, y1, x2, y2 = bbox
        y2 = min(y2 + extra_margin, frame.shape[0])
        coord_list[idx] = [x1, y1, x2, y2]
        crop = cv2.resize(frame[y1:y2, x1:x2], (256, 256), interpolation=cv2.INTER_LANCZOS4)
        latents = vae.get_latents_for_unet(crop)
        input_latent_list.append(latents)

    # Remove frames where face was not detected
    valid = [(c, f, l) for c, f, l in zip(coord_list, frame_list, input_latent_list) if l is not None]
    if not valid:
        raise RuntimeError("No faces detected in source video. Check the source file.")
    coord_list_clean, frame_list_clean, latent_list_clean = zip(*valid)

    # Ping-pong cycle for seamless loop
    frame_list_cycle = list(frame_list_clean) + list(frame_list_clean)[::-1]
    coord_list_cycle = list(coord_list_clean) + list(coord_list_clean)[::-1]
    latent_list_cycle = list(latent_list_clean) + list(latent_list_clean)[::-1]

    # ---------- Step 4: masks and save ----------
    print("Generating blending masks ...")
    mask_coords_list_cycle = []
    mask_list_cycle = []

    for i, (frame, coord) in enumerate(tqdm(zip(frame_list_cycle, coord_list_cycle), total=len(frame_list_cycle))):
        cv2.imwrite(str(full_imgs_path / f"{i:08d}.png"), frame)
        x1, y1, x2, y2 = coord
        mask, crop_box = get_image_prepare_material(frame, [x1, y1, x2, y2], fp=fp, mode=parsing_mode)
        cv2.imwrite(str(mask_out_path / f"{i:08d}.png"), mask)
        mask_coords_list_cycle.append(crop_box)
        mask_list_cycle.append(mask)

    with open(avatar_dir / "coords.pkl", "wb") as f:
        pickle.dump(coord_list_cycle, f)
    with open(avatar_dir / "mask_coords.pkl", "wb") as f:
        pickle.dump(mask_coords_list_cycle, f)
    torch.save(latent_list_cycle, str(avatar_dir / "latents.pt"))

    meta = {
        "name": name,
        "source": str(source),
        "bbox_shift": bbox_shift,
        "extra_margin": extra_margin,
        "parsing_mode": parsing_mode,
        "n_frames_cycle": len(frame_list_cycle),
        "version": "v15",
    }
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)

    print(f"\n✅ Avatar '{name}' baked → {avatar_dir}")
    print(f"   {len(frame_list_cycle)} frames in cycle, latents.pt written")
    return avatar_dir


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Bake MuseTalk per-identity cache")
    parser.add_argument("--name", required=True, help="Avatar identity name (used as dir name)")
    parser.add_argument("--source", required=True, help="Source mp4 video or portrait image path")
    parser.add_argument("--bbox-shift", type=int, default=0, help="Vertical bbox adjustment (+ = expand down)")
    parser.add_argument("--extra-margin", type=int, default=10, help="Extra pixels added below face bbox")
    parser.add_argument("--parsing-mode", default="jaw", choices=["jaw", "raw"], help="Face blending mode")
    parser.add_argument("--device", default="cuda", help="torch device")
    parser.add_argument("--force", action="store_true", help="Re-create even if avatar already exists")
    args = parser.parse_args()

    bake_avatar(
        name=args.name,
        source=args.source,
        bbox_shift=args.bbox_shift,
        extra_margin=args.extra_margin,
        parsing_mode=args.parsing_mode,
        device=args.device,
        force=args.force,
    )
