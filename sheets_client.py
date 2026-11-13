"""Читання посилань і запис результатів у Google Таблицю."""

from __future__ import annotations

import time

import gspread
from google.oauth2.service_account import Credentials
from gspread.exceptions import APIError

from config import Settings

SCOPES = (
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
)


class SheetsClient:
    def __init__(self, settings: Settings) -> None:
        credentials = Credentials.from_service_account_file(
            str(settings.credentials_file),
            scopes=SCOPES,
        )
        self.service_account_email = credentials.service_account_email
        client = gspread.authorize(credentials)
        spreadsheet = client.open(settings.spreadsheet_name)
        self._worksheet = spreadsheet.sheet1

    def list_product_links(self) -> list[tuple[int, str]]:
        rows = self._worksheet.get_all_values()
        links: list[tuple[int, str]] = []
        for row_number, row in enumerate(rows[1:], start=2):
            raw = row[0] if row else ""
            url = raw.strip().strip("\"'")
            if url:
                links.append((row_number, url))
        return links

    def update_product_row(
        self,
        row_number: int,
        name: str,
        price: int | float | str,
        availability: str,
        checked_at: str,
    ) -> None:
        values = [[name, price, availability, checked_at]]
        range_name = f"B{row_number}:E{row_number}"
        for attempt in range(2):
            try:
                self._worksheet.update(values, range_name, raw=True)
                return
            except APIError as exc:
                if exc.code == 429 and attempt == 0:
                    print("Перевищено ліміт запитів Google Sheets. Повтор через 20 с.")
                    time.sleep(20)
                    continue
                raise
