# ============================================================
# 临时草稿 — 小票工具 Phase 2 批量处理
# 用完删除此文件
# ============================================================
#
# 改动清单:
#   1. config.py       — 加 2 个常量
#   2. normalize.py    — 新文件: PDF 拆页 + 缩略图
#   3. excel_writer.py — 去掉 source_file 参数,改由 pipeline 填
#   4. pipeline.py     — 加 process_folder,改 process_single_file
#   5. __main__.py     — 支持文件夹参数


# ============================================================
# FILE 1: config.py  (在文件末尾加 2 行)
# ============================================================
# 现有内容不动,在最后追加:

THUMBNAIL_WIDTH = 600
PDF_RENDER_DPI = 150


# ============================================================
# FILE 2: normalize.py  (全新文件)
# ============================================================

"""PDF 拆页 + 图片统一 + 缩略图生成"""

from dataclasses import dataclass
from pathlib import Path

try:
    from pdf2image import convert_from_path
except ImportError:
    convert_from_path = None

from PIL import Image

from .config import PDF_RENDER_DPI, THUMBNAIL_WIDTH


@dataclass
class NormalizedPage:
    source_file: str
    page_num: int
    image_path: Path
    thumbnail_path: Path


def _check_poppler():
    if convert_from_path is None:
        print("ERROR: pdf2image not installed.")
        print("  Run: pip install pdf2image")
        print("  Then install poppler for Windows:")
        print("  1. Download from https://github.com/oschwartz10612/poppler-windows/releases")
        print("  2. Extract to e.g. C:\\poppler")
        print("  3. Add C:\\poppler\\Library\\bin to your system PATH")
        raise SystemExit(1)


def _make_thumbnail(img: Image.Image, thumb_path: Path):
    thumb = img.copy()
    thumb.thumbnail((THUMBNAIL_WIDTH, THUMBNAIL_WIDTH * 10))
    thumb.save(str(thumb_path), "PNG")


def normalize_file(file_path: Path, work_dir: Path) -> list[NormalizedPage]:
    pages_dir = work_dir / "pages"
    thumbs_dir = work_dir / "thumbnails"
    pages_dir.mkdir(exist_ok=True)
    thumbs_dir.mkdir(exist_ok=True)

    ext = file_path.suffix.lower()
    stem = file_path.stem

    if ext == ".pdf":
        _check_poppler()
        images = convert_from_path(str(file_path), dpi=PDF_RENDER_DPI)
        pages = []
        for i, img in enumerate(images, start=1):
            page_name = f"{stem}_p{i}.png"
            page_path = pages_dir / page_name
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


# ============================================================
# FILE 3: excel_writer.py  (改动 write_excel 签名 + 循环)
# ============================================================
# 完整替换为:

from pathlib import Path
from openpyxl import Workbook
from .config import EXCEL_COLUMNS

def write_excel(transactions: list[dict], output_path: Path) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "Results"
    ws.append(EXCEL_COLUMNS)
    for tx in transactions:
        row = []
        for col in EXCEL_COLUMNS:
            if col == "needs_review":
                row.append(False)
            else:
                row.append(tx.get(col))
        ws.append(row)
    wb.save(output_path)
    return output_path


# ============================================================
# FILE 4: pipeline.py  (加 process_folder, 改 process_single_file)
# ============================================================
# 完整替换为:

from pathlib import Path
from . import extract, excel_writer, normalize

SUPPORTED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png"}


def process_single_file(file_path: Path, output_dir: Path) -> Path:
    transactions = extract.extract_from_file(file_path)
    for tx in transactions:
        tx["source_file"] = file_path.name
        tx["page_num"] = 1
        tx["thumbnail_path"] = ""
    print(f"Extracted {len(transactions)} transaction(s)")
    excel_path = output_dir / "results.xlsx"
    excel_writer.write_excel(transactions, excel_path)
    return excel_path


def process_folder(folder_path: Path, output_dir: Path) -> Path:
    files = sorted([
        f for f in folder_path.iterdir()
        if f.suffix.lower() in SUPPORTED_EXTENSIONS
    ])
    total = len(files)
    if total == 0:
        print("No supported files found.")
        return None

    print(f"Found {total} file(s) to process.")

    all_transactions = []

    for idx, f in enumerate(files, start=1):
        pages = normalize.normalize_file(f, output_dir)
        file_tx_count = 0
        for page in pages:
            transactions = extract.extract_from_file(page.image_path)
            for tx in transactions:
                tx["source_file"] = page.source_file
                tx["page_num"] = page.page_num
                tx["thumbnail_path"] = str(
                    page.thumbnail_path.relative_to(output_dir)
                )
            file_tx_count += len(transactions)
            all_transactions.extend(transactions)
        print(
            f"[{idx}/{total}] {f.name} "
            f"({len(pages)} page(s)) -> {file_tx_count} transaction(s)"
        )

    excel_path = output_dir / "results.xlsx"
    excel_writer.write_excel(all_transactions, excel_path)
    print(f"\nDone! {len(all_transactions)} total transaction(s) -> {excel_path}")
    return excel_path


# ============================================================
# FILE 5: __main__.py  (支持文件夹 + 文件)
# ============================================================
# 完整替换为:

import sys
from datetime import datetime
from pathlib import Path

from .pipeline import process_folder, process_single_file


def main():
    if len(sys.argv) < 2:
        print("Usage: python -m receipts <file_or_folder>")
        sys.exit(1)

    input_path = Path(sys.argv[1]).resolve()
    if not input_path.exists():
        print(f"ERROR: path not found: {input_path}")
        sys.exit(1)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = input_path.parent / f"output_{timestamp}"
    output_dir.mkdir(exist_ok=True)

    if input_path.is_dir():
        print(f"Processing folder: {input_path}")
        process_folder(input_path, output_dir)
    else:
        print(f"Processing {input_path.name} ...")
        excel_path = process_single_file(input_path, output_dir)
        print(f"Done! Output: {excel_path}")


if __name__ == "__main__":
    main()
