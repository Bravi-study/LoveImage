"""Turns an untrusted image from stdin into a clean JPEG on stdout. Any failure is a non-zero exit."""

import io
import sys

from PIL import Image, ImageOps
from pillow_heif import register_heif_opener

MAX_PIXELS = 64_000_000
FORMATS = ["JPEG", "PNG", "WEBP", "HEIF"]


def main():
    max_side = int(sys.argv[1])
    register_heif_opener()
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    img = Image.open(io.BytesIO(sys.stdin.buffer.read()), formats=FORMATS)
    # Pillow only warns up to twice MAX_IMAGE_PIXELS.
    if img.width * img.height > MAX_PIXELS:
        sys.exit("too many pixels")
    # Shrunk first: a 64 MP picture of a few hundred kilobytes would otherwise take a gigabyte in the steps below.
    # Palette images would be resampled crudely here, so they wait for the last step.
    if img.mode in ("RGB", "RGBA", "L", "LA"):
        img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    img = ImageOps.exif_transpose(img).convert("RGBA")
    # A fresh canvas carries no EXIF, GPS or comments from the source.
    clean = Image.alpha_composite(Image.new("RGBA", img.size, "white"), img).convert("RGB")
    clean.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    clean.save(sys.stdout.buffer, "JPEG", quality=90)


main()
