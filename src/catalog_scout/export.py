import csv
import os
from dataclasses import astuple, fields
from pathlib import Path
from tempfile import NamedTemporaryFile

from .http import CrawlError
from .product import Product

COLUMNS = tuple(field.name for field in fields(Product))


def check_output(path: Path) -> None:
    if path.suffix.lower() not in {".csv", ".xlsx"}:
        raise CrawlError("Output filename must end in .csv or .xlsx")
    if path.suffix.lower() == ".xlsx":
        try:
            import openpyxl  # noqa: F401
        except ImportError as exc:
            raise CrawlError("Excel export requires python -m pip install '.[excel]'") from exc


def _csv_value(value: object) -> object:
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r", "\n")):
        return "'" + value
    return value


def _write_csv(path: Path, products: list[Product]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(COLUMNS)
        writer.writerows([_csv_value(value) for value in astuple(product)] for product in products)


def _write_xlsx(path: Path, products: list[Product]) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Products"
    sheet.append(COLUMNS)
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="174B52")
        cell.alignment = Alignment(vertical="center")
    sheet.row_dimensions[1].height = 23
    sheet.freeze_panes = "C2"

    for product in products:
        sheet.append(astuple(product))
        for cell in sheet[sheet.max_row]:
            if isinstance(cell.value, str):
                cell.data_type = "s"
            if COLUMNS[cell.column - 1] in {"price", "original_price"}:
                cell.number_format = "0.00"

    widths = (13, 21, 43, 21, 12, 17, 12, 10, 17, 19, 49, 68)
    for number, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(number)].width = width
    sheet.auto_filter.ref = sheet.dimensions
    workbook.save(path)


def export_products(path: Path, products: list[Product]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, prefix=".catalog-scout-", delete=False) as stream:
        temporary = Path(stream.name)
    try:
        if path.suffix.lower() == ".csv":
            _write_csv(temporary, products)
        else:
            _write_xlsx(temporary, products)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
