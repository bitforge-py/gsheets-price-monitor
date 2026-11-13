"""Моніторинг цін товарів за посиланнями з Google Таблиці."""

from __future__ import annotations

import sys
import time
from datetime import datetime
from urllib.parse import urlsplit

from gspread.exceptions import APIError, SpreadsheetNotFound

from config import load_settings
from parser import BrowserStartError, ParseError, PriceParser
from sheets_client import SheetsClient

REQUEST_PAUSE_SECONDS = 1


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8")
        except (OSError, ValueError):
            continue


def format_checked_at() -> str:
    return datetime.now().strftime("%d.%m.%Y %H:%M")


def format_price(price: int | float | str) -> str:
    if price == "":
        return "не знайдено"
    if isinstance(price, (int, float)):
        return f"{price} грн"
    return str(price)


def is_http_url(value: str) -> bool:
    parsed = urlsplit(value.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def write_row(
    sheets: SheetsClient,
    row_number: int,
    name: str,
    price: int | float | str,
    availability: str,
    checked_at: str,
) -> bool:
    try:
        sheets.update_product_row(row_number, name, price, availability, checked_at)
    except APIError as exc:
        print(f"  Не вдалося записати рядок {row_number}: {exc}")
        return False
    except Exception as exc:
        print(f"  Не вдалося записати рядок {row_number}: {exc}")
        return False
    return True


def main() -> int:
    configure_stdio()
    settings = load_settings()
    print(f"Підключення до таблиці «{settings.spreadsheet_name}»...")

    try:
        sheets = SheetsClient(settings)
    except SpreadsheetNotFound:
        print(
            f"Таблицю «{settings.spreadsheet_name}» не знайдено. "
            "Перевірте назву у .env і чи надано сервісному акаунту доступ редактора."
        )
        return 1
    except Exception as exc:
        print(f"Не вдалося відкрити таблицю: {exc}")
        return 1

    print(f"Сервісний акаунт: {sheets.service_account_email}")
    links = sheets.list_product_links()
    print(f"Знайдено посилань: {len(links)}")
    if not links:
        print("У стовпці A, починаючи з рядка 2, немає посилань.")
        print("Заголовки: A - посилання, B - назва, C - ціна, D - наявність, E - час перевірки.")
        return 0

    success = 0
    failed = 0
    total = len(links)
    try:
        with PriceParser() as parser:
            for index, (row_number, url) in enumerate(links, start=1):
                print(f"[{index}/{total}] Рядок {row_number}: {url}")
                checked_at = format_checked_at()
                if not is_http_url(url):
                    failed += 1
                    print("  Посилання має починатися з http:// або https://.")
                    write_row(sheets, row_number, "Некоректне посилання", "", "Помилка", checked_at)
                else:
                    try:
                        product = parser.parse(url)
                    except ParseError as exc:
                        failed += 1
                        print(f"  {exc}")
                        write_row(sheets, row_number, str(exc), "", "Помилка", checked_at)
                    except Exception as exc:
                        failed += 1
                        detail = " ".join(str(exc).split())
                        if len(detail) > 180:
                            detail = detail[:177] + "..."
                        message = f"Неочікувана помилка: {detail}"
                        print(f"  {message}")
                        write_row(sheets, row_number, message, "", "Помилка", checked_at)
                    else:
                        written = write_row(
                            sheets,
                            row_number,
                            product.name,
                            product.price,
                            product.availability,
                            checked_at,
                        )
                        if written:
                            success += 1
                            print(f"  Назва: {product.name or 'не знайдено'}")
                            print(f"  Ціна: {format_price(product.price)}")
                            print(f"  Наявність: {product.availability}")
                        else:
                            failed += 1

                if index != total:
                    time.sleep(REQUEST_PAUSE_SECONDS)
    except BrowserStartError as exc:
        print(str(exc))
        return 1

    print(f"Готово. Успішно: {success}. З помилками: {failed}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
