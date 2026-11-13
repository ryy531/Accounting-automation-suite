"""PDF 拆页 + 图片统一 + 缩略图生成"""

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from pdf2image import convert_from_path
except ImportError:
    convert_from_path = None

from PIL import Image
from .config import PDF_RENDER_DPI, POPPLER_PATH, THUMBNAIL_WIDTH


@dataclass
class NormalizedPage:
    source_file : str
    page_num : int
    image_path : Path
    thumbnail_path : Path


def _resolve_poppler_path() -> str | None:
    """Resolve Poppler bin directory. Returns None to let pdf2image use PATH."""
    # 1. Environment variable override
    env = os.environ.get("POPPLER_PATH")
    if env and Path(env).is_dir():
        return env

    # 2. Config value (C:\poppler\Library\bin)
    if Path(POPPLER_PATH).is_dir():
        return POPPLER_PATH

    # 3. Winget default install path for current user
    user = os.environ.get("USERNAME", "")
    winget = Path(
        f"C:\\Users\\{user}\\AppData\\Local\\Microsoft\\WinGet\\Packages"
        f"\\oschwartz10612.Poppler_Microsoft.Winget.Source_8wekyb3d8bbwe"
    )
    if winget.is_dir():
        bins = sorted(winget.glob("poppler-*/Library/bin"))
        if bins:
            return str(bins[-1])

    # 4. Fall back — let pdf2image search PATH
    return None


_RESOLVED_POPPLER = _resolve_poppler_path()


def _check_poppler():
    if convert_from_path is None:
        print ("ERROR: pdf2image not installed.")
        print("  Run : pip install pdf2image")
        print("  Then install poppler for Windows:")
        print("  1. Download from https://github.com/oschwartz10612/poppler-windows/releases")
        print("  2. Extract to e.g. C:\\poppler")
        print("  3. Add C:\\poppler\\Library\\bin to your system PATH")
        raise SystemExit(1)
    

def _make_thumbnail(img:Image.Image, thumb_path : Path):
    thumb = img.copy()
    thumb.thumbnail((THUMBNAIL_WIDTH, THUMBNAIL_WIDTH * 10))
    thumb.save(str(thumb_path), "PNG")


def normalize_file(file_path : Path, work_dir : Path) -> list[NormalizedPage]:
    pages_dir = work_dir / "pages"
    thumbs_dir = work_dir / "thumbnails"
    pages_dir.mkdir(exist_ok=True)
    thumbs_dir.mkdir(exist_ok=True)

    ext = file_path.suffix.lower()
    stem = file_path.stem
    if ext == ".pdf":
        _check_poppler()
        print(f"  Poppler path: {_RESOLVED_POPPLER or '(using PATH)'}")
        try:
            images = convert_from_path(
                str(file_path), dpi=PDF_RENDER_DPI,
                poppler_path=_RESOLVED_POPPLER,
            )
        except Exception as e:
            raise RuntimeError(
                f"PDF conversion failed (poppler_path={_RESOLVED_POPPLER}): {e}"
            ) from e
        pages = []
        for i , img in enumerate(images, start=1):
            page_name = f"{stem}_p{i}.png"
            page_path = pages_dir/page_name
            img.save(str(page_path), "PNG")
            thumb_path = thumbs_dir / page_name
            _make_thumbnail(img, thumb_path)
            pages.append(NormalizedPage(
                source_file=file_path.name,
                page_num=i,
                image_path=page_path,
                thumbnail_path=thumb_path,
            ))
        return pages
    
    elif ext in (".jpg", ".jpeg", ".png"):
        img = Image.open(str(file_path))
        page_name = f"{stem}_p1.png"
        page_path = pages_dir / page_name
        img.save(str(page_path), "PNG")
        thumb_path = thumbs_dir / page_name
        _make_thumbnail(img, thumb_path)
        return [NormalizedPage(
            source_file=file_path.name,
            page_num=1,
            image_path=page_path,
            thumbnail_path=thumb_path,
        )]
    
    else:
        raise ValueError(f"Unsupported file type: {ext}")