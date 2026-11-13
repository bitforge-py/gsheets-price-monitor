"""Завантаження налаштувань із файлу .env."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent
load_dotenv(ROOT_DIR / ".env")


@dataclass(frozen=True, slots=True)
class Settings:
    spreadsheet_name: str
    credentials_file: Path


def load_settings() -> Settings:
    spreadsheet_name = os.getenv("SPREADSHEET_NAME", "").strip()
    credentials_name = os.getenv("CREDENTIALS_FILE", "credentials.json").strip()
    if not spreadsheet_name:
        raise SystemExit("У файлі .env не задано SPREADSHEET_NAME.")

    credentials_file = Path(credentials_name)
    if not credentials_file.is_absolute():
        credentials_file = ROOT_DIR / credentials_file
    if not credentials_file.is_file():
        raise SystemExit(
            "Файл облікових даних не знайдено: "
            f"{credentials_file}. Скопіюйте credentials.example.json у credentials.json "
            "і вставте ключ сервісного акаунта."
        )

    return Settings(
        spreadsheet_name=spreadsheet_name,
        credentials_file=credentials_file,
    )
