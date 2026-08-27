"""素材规格 preflight 检查与自动修复（按 provider 图片规格）。

在调用 generate.py 之前运行：逐图校验所选 provider 的图片规格
（--provider minimax_h3 或 seedance；H3 每边 [256, 5760]px，Seedance
[300, 6000]px，比例均 [0.4, 2.5]），超限图自动修复（比例中心裁剪、边长
缩放、格式转 PNG），修复文件写入 --out-dir。不调用任何生成 API，不产生
费用；超限素材在本地处理完毕后再进生成，避免 API 端报错导致重复扣生成次数。

用法（供 SKILL 指引，勿直接读源码）：
python /mnt/skills/public/video-generation/scripts/check_materials.py \
  --images /mnt/user-data/uploads/a.png /mnt/user-data/uploads/b.jpg \
  --out-dir /mnt/user-data/workspace
# Seedance 素材：加 --provider seedance

输出：逐图状态行 + 一行 ready 列表（修复后可直接拼进 generate.py 的
--reference-images）。无法解码的图报错退出（需替换素材），超限已修复
则正常退出。
"""

import argparse
import math
import os
import sys
from dataclasses import dataclass
from pathlib import Path

try:
    from PIL import Image, ImageOps
except ImportError:
    Image = None
    ImageOps = None

import requests

# ── 图片规格表（per provider）───────────────────────────────────────────
# 官方源：MiniMax H3（宽高 [256, 5760]px、比例 5:2~2:5）；
# Seedance API Ref（宽高 [300, 6000]px、比例 [0.4, 2.5]）。
# 不在 extensions 内的格式解码后转 PNG。
# base64 传输膨胀约 33%，请求体上限 64MB —— 本地图总大小软顶，超出提示改用公网 URL
_REQUEST_BODY_SOFT_LIMIT = 45 * 1024 * 1024


@dataclass(frozen=True)
class _ImageSpec:
    side_min: int
    side_max: int
    ratio_min: float
    ratio_max: float
    extensions: frozenset[str]
    label: str


IMAGE_SPECS: dict[str, _ImageSpec] = {
    "minimax_h3": _ImageSpec(
        side_min=256,
        side_max=5760,
        ratio_min=0.4,
        ratio_max=2.5,
        extensions=frozenset({".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}),
        label="MiniMax-H3",
    ),
    "seedance": _ImageSpec(
        side_min=300,
        side_max=6000,
        ratio_min=0.4,
        ratio_max=2.5,
        extensions=frozenset({".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff", ".gif", ".heic", ".heif"}),
        label="Seedance",
    ),
}


def _fail(message: str) -> None:
    print(message, file=sys.stderr)
    sys.exit(2)


def probe_image(path: str) -> tuple[int, int, str, str]:
    """返回 (w, h, 后缀, 错误信息)；错误信息为空表示成功。"""
    if Image is None:
        return 0, 0, "", "PIL not installed in this environment"
    try:
        with Image.open(path) as im:
            # 手机照片 EXIF 方向旋转后才是真实宽高
            im = ImageOps.exif_transpose(im)
            return im.size[0], im.size[1], Path(path).suffix.lower(), ""
    except Exception as e:
        return 0, 0, "", f"cannot decode image: {e}"


def _to_rgb(im):
    """透明图输出 JPG/WEBP 前贴白底，避免透明区域变黑。"""
    if im.mode in ("RGBA", "LA"):
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.split()[-1])
        return bg
    return im.convert("RGB")


def fix_image(path: str, out_dir: str, spec: _ImageSpec) -> tuple[str, list[str]]:
    """修复单张图，返回 (输出路径, 操作列表)；无修改时返回原路径。"""
    ops: list[str] = []
    im = Image.open(path)
    im = ImageOps.exif_transpose(im)
    w, h = im.size

    if Path(path).suffix.lower() not in spec.extensions:
        ops.append("convert-format")

    if min(w, h) < spec.side_min:
        scale = spec.side_min / min(w, h)
        im = im.resize(
            (max(1, round(w * scale)), max(1, round(h * scale))),
            Image.LANCZOS,
        )
        ops.append(f"upscale-to-{spec.side_min}")
    if max(w, h) > spec.side_max:
        scale = spec.side_max / max(w, h)
        im = im.resize(
            (max(1, round(w * scale)), max(1, round(h * scale))),
            Image.LANCZOS,
        )
        ops.append(f"downscale-to-{spec.side_max}")

    w, h = im.size
    ratio = w / h
    if ratio > spec.ratio_max:
        # 太宽：上下补白边降比例（保留原图，不裁剪丢信息）；ceil 保证 ratio 不超
        new_h = max(1, math.ceil(w / spec.ratio_max))
        canvas = Image.new("RGB", (w, new_h), (255, 255, 255))
        canvas.paste(_to_rgb(im), (0, (new_h - h) // 2))
        im = canvas
        ops.append("pad-height")
    elif ratio < spec.ratio_min:
        # 太高：左右补白边升比例；ceil 保证 ratio 不低于 min
        new_w = max(1, math.ceil(h * spec.ratio_min))
        canvas = Image.new("RGB", (new_w, h), (255, 255, 255))
        canvas.paste(_to_rgb(im), ((new_w - w) // 2, 0))
        im = canvas
        ops.append("pad-width")
    # pad 后若超 side_max，整体缩小（不裁剪信息）
    if max(im.size) > spec.side_max:
        scale = spec.side_max / max(im.size)
        im = im.resize((max(1, round(im.size[0] * scale)), max(1, round(im.size[1] * scale))), Image.LANCZOS)
        ops.append(f"downscale-to-{spec.side_max}")

    if not ops:
        return path, ops

    ext = Path(path).suffix.lower()
    if ext not in (".png", ".jpg", ".jpeg", ".webp"):
        # HEIC/HEIF 等：无法可靠编码，统一转 PNG
        ext = ".png"
    out_path = os.path.join(out_dir, f"{Path(path).stem}-fixed{ext}")
    if ext == ".jpg" or ext == ".jpeg":
        im = _to_rgb(im)
        im.save(out_path, "JPEG", quality=95)
    elif ext == ".webp":
        im = _to_rgb(im)
        im.save(out_path, "WEBP", quality=95)
    else:
        im.save(out_path, "PNG")
    return out_path, ops


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--images", nargs="*", default=[], required=True, help="Absolute paths or public URLs")
    parser.add_argument("--out-dir", required=True, help="Directory for fixed files")
    parser.add_argument(
        "--provider",
        default="minimax_h3",
        choices=sorted(IMAGE_SPECS),
        help="Image-spec set to check against (minimax_h3 or seedance)",
    )
    args = parser.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)

    if not args.images:
        _fail("Error: --images is empty; pass at least one image path or URL")

    spec = IMAGE_SPECS[args.provider]
    ready: list[str] = []
    total_bytes = 0
    print(f"== material check: {len(args.images)} image(s) vs {spec.label} specs (side [{spec.side_min},{spec.side_max}]px, ratio [{spec.ratio_min},{spec.ratio_max}]) ==")
    for src in args.images:
        if src.startswith(("http://", "https://")):
            name = os.path.basename(src.split("?")[0]) or "remote-image"
            local = os.path.join(args.out_dir, name)
            try:
                resp = requests.get(src, timeout=60)
                resp.raise_for_status()
            except Exception as e:
                _fail(f"[error] {src}: cannot download: {e}")
            with open(local, "wb") as f:
                f.write(resp.content)
            print(f"[download] {src} -> {local}")
            src = local

        w, h, ext, err = probe_image(src)
        if err:
            _fail(f"[error] {src}: {err} - replace this material before generating")
        size = os.path.getsize(src)
        total_bytes += size
        ratio = w / h

        out_path, ops = fix_image(src, args.out_dir, spec)
        if ops:
            fw, fh, _, ferr = probe_image(out_path)
            if ferr:
                _fail(f"[error] fixed output unreadable: {out_path}: {ferr}")
            print(f"[fixed] {src} ({w}x{h}, ratio {ratio:.3f}) -> {out_path} ({fw}x{fh}, ratio {fw / fh:.3f}) [{', '.join(ops)}]")
        else:
            print(f"[ok]    {src} ({w}x{h}, ratio {ratio:.3f})")
        ready.append(out_path)

    if total_bytes > _REQUEST_BODY_SOFT_LIMIT:
        print(f"!! local images total {total_bytes / 1024 / 1024:.0f} MB; base64 body nears the 64 MB request cap — host large files at a public URL instead")
    print("ready:", " ".join(ready))


if __name__ == "__main__":
    main()
