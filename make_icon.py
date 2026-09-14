#!/usr/bin/env python3
"""Generate Archivex icon — sledgehammer smashing a stack of files.
The hammer and debris break out of the rounded-square backdrop for a
more dynamic silhouette (macOS icons often have elements crossing bounds)."""

import math
import os
import shutil
import subprocess

from PIL import Image, ImageDraw, ImageFilter


def _file_shape(d, s, x, y, w, h, fill, fold):
    ear = s * 0.035
    x0, y0 = s * x, s * y
    x1, y1 = s * (x + w), s * (y + h)
    d.polygon([
        (x0, y0), (x1 - ear, y0), (x1, y0 + ear),
        (x1, y1), (x0, y1),
    ], fill=fill)
    d.polygon([(x1 - ear, y0), (x1, y0 + ear), (x1 - ear, y0 + ear)], fill=fold)


def draw_icon(size):
    s = size
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))

    # --- rounded-square backdrop with slight vertical gradient ---
    bg = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    bg_draw = ImageDraw.Draw(bg)
    corner_r = s * 0.22
    inset = s * 0.06
    for i in range(int(s * 0.88)):
        t = i / (s * 0.88)
        r = int(24 + t * 8)
        g = int(30 + t * 10)
        b = int(48 + t * 18)
        y = inset + i
        bg_draw.line([(inset, y), (s - inset, y)], fill=(r, g, b, 255))
    # mask to rounded rect
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [inset, inset, s - inset, s - inset],
        radius=corner_r, fill=255,
    )
    bg.putalpha(mask)
    img.paste(bg, (0, 0), bg)

    d = ImageDraw.Draw(img)

    # subtle inner highlight along the top edge
    hl = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    ImageDraw.Draw(hl).rounded_rectangle(
        [inset + s * 0.02, inset + s * 0.02,
         s - inset - s * 0.02, inset + s * 0.20],
        radius=corner_r * 0.8,
        fill=(255, 255, 255, 22),
    )
    img.alpha_composite(hl)

    # --- file stack (offset slightly right for balance) ---
    _file_shape(d, s, 0.24, 0.68, 0.56, 0.13, (150, 165, 192), (120, 135, 162))
    _file_shape(d, s, 0.22, 0.58, 0.54, 0.12, (178, 192, 216), (148, 162, 186))

    # top file — split by impact
    # left shard
    d.polygon([
        (s * 0.24, s * 0.48), (s * 0.47, s * 0.47),
        (s * 0.46, s * 0.59), (s * 0.23, s * 0.60),
    ], fill=(210, 222, 240))
    # right shard, kicked outward
    d.polygon([
        (s * 0.51, s * 0.46), (s * 0.74, s * 0.49),
        (s * 0.76, s * 0.58), (s * 0.52, s * 0.57),
    ], fill=(195, 208, 230))

    # crack
    crack_w = max(1, int(s * 0.015))
    d.line([(s * 0.49, s * 0.46), (s * 0.49, s * 0.59)],
           fill=(28, 32, 48), width=crack_w)

    # text lines on lower files
    lw = max(1, int(s * 0.008))
    d.line([(s * 0.30, s * 0.63), (s * 0.58, s * 0.63)],
           fill=(148, 162, 186), width=lw)
    d.line([(s * 0.30, s * 0.66), (s * 0.50, s * 0.66)],
           fill=(148, 162, 186), width=lw)
    d.line([(s * 0.30, s * 0.73), (s * 0.60, s * 0.73)],
           fill=(120, 135, 162), width=lw)

    # --- sledgehammer — handle extends beyond top-left of backdrop ---
    angle = math.radians(40)
    cos_a = math.cos(angle)
    sin_a = math.sin(angle)

    # impact point (on the cracked file)
    ix, iy = s * 0.485, s * 0.47

    # handle stretches from impact point up-and-out past the edge
    h_len = s * 0.60
    h_w = s * 0.045
    hx_far = ix - cos_a * h_len
    hy_far = iy - sin_a * h_len
    px, py = -sin_a * h_w / 2, cos_a * h_w / 2

    # handle shadow
    sh_off = s * 0.010
    d.polygon([
        (hx_far - px + sh_off, hy_far - py + sh_off),
        (hx_far + px + sh_off, hy_far + py + sh_off),
        (ix + px + sh_off, iy + py + sh_off),
        (ix - px + sh_off, iy - py + sh_off),
    ], fill=(0, 0, 0, 90))

    # handle body
    d.polygon([
        (hx_far - px, hy_far - py), (hx_far + px, hy_far + py),
        (ix + px, iy + py), (ix - px, iy - py),
    ], fill=(140, 92, 44))

    # handle wood-grain highlight
    px2, py2 = px * 0.35, py * 0.35
    d.polygon([
        (hx_far - px2, hy_far - py2), (hx_far + px2, hy_far + py2),
        (ix + px2, iy + py2), (ix - px2, iy - py2),
    ], fill=(180, 128, 68))

    # grip rings near the far end
    grip_w = max(1, int(s * 0.008))
    for t in [0.05, 0.10, 0.15, 0.20]:
        gx = hx_far + cos_a * h_len * t
        gy = hy_far + sin_a * h_len * t
        d.line([(gx - px * 1.1, gy - py * 1.1),
                (gx + px * 1.1, gy + py * 1.1)],
               fill=(90, 55, 22), width=grip_w)

    # metal collar at hammer-head end
    coll_t = 0.85
    cx1 = hx_far + cos_a * h_len * coll_t
    cy1 = hy_far + sin_a * h_len * coll_t
    d.polygon([
        (cx1 - px * 1.35, cy1 - py * 1.35),
        (cx1 + px * 1.35, cy1 + py * 1.35),
        (ix + px * 1.35, iy + py * 1.35),
        (ix - px * 1.35, iy - py * 1.35),
    ], fill=(75, 82, 96))

    # --- hammer head — perpendicular to handle, centered on impact ---
    perp_x, perp_y = -sin_a, cos_a
    head_long = s * 0.24
    head_short = s * 0.13
    hl = head_long / 2
    hs_back = head_short * 0.30
    hs_fwd = head_short * 0.70

    head = [
        (ix + perp_x * hl - cos_a * hs_back,
         iy + perp_y * hl - sin_a * hs_back),
        (ix + perp_x * hl + cos_a * hs_fwd,
         iy + perp_y * hl + sin_a * hs_fwd),
        (ix - perp_x * hl + cos_a * hs_fwd,
         iy - perp_y * hl + sin_a * hs_fwd),
        (ix - perp_x * hl - cos_a * hs_back,
         iy - perp_y * hl - sin_a * hs_back),
    ]
    # head shadow
    head_sh = [(x + sh_off * 1.5, y + sh_off * 1.5) for x, y in head]
    d.polygon(head_sh, fill=(0, 0, 0, 100))
    d.polygon(head, fill=(95, 105, 122))

    # bevel highlight along one long edge
    bv = 0.20
    face = [
        (ix + perp_x * hl * (1 - bv) + cos_a * hs_fwd * 0.95,
         iy + perp_y * hl * (1 - bv) + sin_a * hs_fwd * 0.95),
        (ix - perp_x * hl * (1 - bv) + cos_a * hs_fwd * 0.95,
         iy - perp_y * hl * (1 - bv) + sin_a * hs_fwd * 0.95),
        (ix - perp_x * hl * (1 - bv) + cos_a * hs_fwd * 0.40,
         iy - perp_y * hl * (1 - bv) + sin_a * hs_fwd * 0.40),
        (ix + perp_x * hl * (1 - bv) + cos_a * hs_fwd * 0.40,
         iy + perp_y * hl * (1 - bv) + sin_a * hs_fwd * 0.40),
    ]
    d.polygon(face, fill=(140, 152, 172))

    # small darker band across the middle of the head
    band = [
        (ix + perp_x * hl * 0.98 + cos_a * hs_fwd * 0.08,
         iy + perp_y * hl * 0.98 + sin_a * hs_fwd * 0.08),
        (ix - perp_x * hl * 0.98 + cos_a * hs_fwd * 0.08,
         iy - perp_y * hl * 0.98 + sin_a * hs_fwd * 0.08),
        (ix - perp_x * hl * 0.98 - cos_a * hs_back * 0.30,
         iy - perp_y * hl * 0.98 - sin_a * hs_back * 0.30),
        (ix + perp_x * hl * 0.98 - cos_a * hs_back * 0.30,
         iy + perp_y * hl * 0.98 - sin_a * hs_back * 0.30),
    ]
    d.polygon(band, fill=(70, 78, 92))

    # --- debris fragments — some outside the backdrop for dynamism ---
    debris = [
        (0.64, 0.28, 0.038, 25),
        (0.72, 0.22, 0.030, 55),
        (0.80, 0.32, 0.024, 10),
        (0.58, 0.20, 0.022, 42),
        (0.86, 0.28, 0.020, 68),   # outside right
        (0.68, 0.15, 0.026, 48),
        (0.32, 0.28, 0.022, 75),
        (0.28, 0.34, 0.017, 20),
        (0.90, 0.42, 0.018, 30),   # outside right
        (0.20, 0.44, 0.016, 88),   # outside left-ish
    ]
    for fx, fy, fs, rot in debris:
        cx, cy = s * fx, s * fy
        r = s * fs
        a = math.radians(rot)
        tri = [
            (cx + r * math.cos(a), cy + r * math.sin(a)),
            (cx + r * math.cos(a + 2.2), cy + r * math.sin(a + 2.2)),
            (cx + r * math.cos(a + 4.3), cy + r * math.sin(a + 4.3)),
        ]
        d.polygon(tri, fill=(215, 226, 242))

    # --- impact sparks (radial gold lines) ---
    spark = (255, 210, 70)
    for angle_deg in [0, 45, 90, 135, 200, 250, 300, 330]:
        a = math.radians(angle_deg)
        r1 = s * 0.055
        r2 = s * 0.115
        x1 = ix + math.cos(a) * r1
        y1 = iy + math.sin(a) * r1
        x2 = ix + math.cos(a) * r2
        y2 = iy + math.sin(a) * r2
        d.line([(x1, y1), (x2, y2)], fill=spark,
               width=max(1, int(s * 0.014)))

    # bright dot at impact center
    dot_r = s * 0.022
    d.ellipse([ix - dot_r, iy - dot_r, ix + dot_r, iy + dot_r],
              fill=(255, 245, 170))

    return img


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    iconset = os.path.join(script_dir, "Archivex.iconset")
    os.makedirs(iconset, exist_ok=True)

    sizes = [16, 32, 64, 128, 256, 512, 1024]
    for sz in sizes:
        icon = draw_icon(sz)
        if sz <= 512:
            icon.save(os.path.join(iconset, f"icon_{sz}x{sz}.png"))
        if sz >= 32:
            half = sz // 2
            icon.save(os.path.join(iconset, f"icon_{half}x{half}@2x.png"))

    icns = os.path.join(script_dir, "icon.icns")
    subprocess.run(["iconutil", "-c", "icns", iconset, "-o", icns], check=True)
    print(f"Created {icns}")
    shutil.rmtree(iconset, ignore_errors=True)


if __name__ == "__main__":
    main()
