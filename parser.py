"""Завантаження сторінки товару і витяг назви, ціни та наявності."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from bs4 import BeautifulSoup
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

# Збігається з Chromium, який ставить Playwright (Chrome 158).
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/158.0.8127.18 Safari/537.36"
)

PRICE_HINT_SELECTOR = (
    "script[type='application/ld+json'], "
    "[itemprop='price'], "
    "meta[property='product:price:amount'], "
    "[data-price], .price"
)

PRICE_SELECTORS = (
    "[itemprop=price]",
    "meta[itemprop=price]",
    "[data-price]",
    "[data-product-price]",
    ".price-value",
    ".product-price",
    ".product__price",
    ".price__current",
    ".current-price",
    ".price-new",
    ".price",
)

AVAILABILITY_SELECTORS = (
    "[itemprop=availability]",
    "[data-availability]",
    ".availability",
    ".stock",
    ".product-status",
    ".product__status",
    ".product-availability",
)

_PRICE_TOKEN_RE = re.compile(
    r"(?<!\d)(?:"
    r"\d{1,3}(?:[ \u00a0\u202f]\d{3})+(?:[.,]\d{2})?"
    r"|\d{1,3}(?:,\d{3})+(?:\.\d{2})?"
    r"|\d{1,3}(?:\.\d{3})+(?:,\d{2})?"
    r"|\d+(?:[.,]\d{1,2})?"
    r")(?!\d)"
)

_OUT_OF_STOCK_PHRASES = (
    "outofstock",
    "soldout",
    "sold out",
    "немає в наявності",
    "немає у наявності",
    "нема в наявності",
    "нема у наявності",
    "не в наявності",
    "немає на складі",
    "нет в наличии",
    "відсутн",
    "закінчив",
    "закінчився",
)

_PREORDER_PHRASES = (
    "preorder",
    "backorder",
    "під замовлення",
    "передзамовлення",
    "очікується",
    "под заказ",
)

_LIMITED_PHRASES = (
    "limitedavailability",
    "закінчується",
    "мало в наявності",
    "останн",
)

_IN_STOCK_PHRASES = (
    "instock",
    "instoreonly",
    "onlineonly",
    "в наявності",
    "є в наявності",
    "в наличии",
    "готовий до відправки",
    "готово до відправки",
)


class BrowserStartError(RuntimeError):
    """Браузер Chromium не вдалося відкрити."""


class ParseError(RuntimeError):
    """Сторінку товару не вдалося прочитати."""


@dataclass(slots=True)
class ProductInfo:
    name: str
    price: int | float | str
    availability: str


class PriceParser:
    def __init__(self) -> None:
        self._stealth = Stealth(
            navigator_languages_override=("uk-UA", "uk"),
            navigator_platform_override="Win32",
            navigator_user_agent_override=USER_AGENT,
            navigator_vendor_override="Google Inc.",
        )
        self._manager: Any = None
        self._browser: Any = None
        self._context: Any = None

    def __enter__(self) -> PriceParser:
        self._manager = self._stealth.use_sync(sync_playwright())
        try:
            playwright = self._manager.__enter__()
        except Exception as exc:
            self._manager = None
            raise self._browser_error(exc) from exc

        try:
            self._browser = playwright.chromium.launch(headless=True)
            self._context = self._browser.new_context(
                user_agent=USER_AGENT,
                locale="uk-UA",
                timezone_id="Europe/Kyiv",
                viewport={"width": 1366, "height": 768},
                extra_http_headers={
                    "Accept-Language": "uk-UA,uk;q=0.9,en-US;q=0.8,en;q=0.7",
                },
            )
            self._context.set_default_navigation_timeout(60_000)
        except Exception as exc:
            self.close()
            raise self._browser_error(exc) from exc
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    @staticmethod
    def _browser_error(exc: Exception) -> BrowserStartError:
        detail = " ".join(str(exc).split())
        if len(detail) > 300:
            detail = detail[:297] + "..."
        return BrowserStartError(
            "Не вдалося запустити браузер Chromium. "
            "Встановіть його командою python -m playwright install chromium. "
            f"Деталі: {detail}"
        )

    def close(self) -> None:
        for resource in (self._context, self._browser):
            if resource is None:
                continue
            try:
                resource.close()
            except Exception:
                pass
        self._context = None
        self._browser = None
        manager = self._manager
        self._manager = None
        if manager is not None:
            manager.__exit__(None, None, None)

    def parse(self, url: str) -> ProductInfo:
        if self._context is None:
            raise BrowserStartError("Браузер не запущено.")

        page = self._context.new_page()
        try:
            try:
                response = page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            except PlaywrightTimeoutError as exc:
                raise ParseError("Час очікування сторінки вичерпано.") from exc
            except Exception as exc:
                raise ParseError("Не вдалося відкрити сторінку.") from exc

            if response is not None and response.status >= 400:
                raise ParseError(f"Сторінка відповіла кодом {response.status}.")

            try:
                page.wait_for_selector(PRICE_HINT_SELECTOR, timeout=8_000)
            except PlaywrightTimeoutError:
                pass

            html = page.content()
        finally:
            page.close()

        product = parse_product_html(html)
        if not product.name and product.price == "":
            raise ParseError("На сторінці не знайдено назву і ціну товару.")
        return product


def parse_product_html(html: str) -> ProductInfo:
    soup = BeautifulSoup(html, "html.parser")
    scope = _pick_scope(soup)
    name, price, availability, currency = _from_json_ld(soup)

    if not name:
        name = _heading(scope, soup) or _meta_content(soup, prop="og:title")
    if not name:
        name = _meta_content(soup, name="twitter:title")
    if not name and soup.title and soup.title.string:
        name = _title_product_name(soup.title.string)

    if _is_blank_price(price):
        raw_price = _meta_content(soup, prop="product:price:amount")
        price = _extract_price_value(raw_price)
        if not currency:
            currency = _meta_content(soup, prop="product:price:currency")
    if _is_blank_price(price):
        price = _price_from_selectors(scope, soup)

    if availability == "Невідомо":
        availability = _availability_from_dom(scope, soup)

    priced = price if _is_blank_price(price) else _apply_currency(price, currency)
    return ProductInfo(
        name=_clip(name),
        price="" if priced is None else _clip_price(priced),
        availability=availability or "Невідомо",
    )


def _from_json_ld(soup: BeautifulSoup) -> tuple[str, int | float | str | None, str, str]:
    products: list[dict[str, Any]] = []
    for script in soup.select("script[type='application/ld+json']"):
        raw = script.string or script.get_text()
        if not raw or not raw.strip():
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for node in _iter_nodes(payload):
            if _has_type(node, "Product"):
                products.append(node)

    chosen: dict[str, Any] | None = None
    for product in products:
        if not _is_blank_price(_offer_field(product.get("offers"), ("price", "lowPrice"))):
            chosen = product
            break
    if chosen is None and products:
        chosen = products[0]
    if chosen is None:
        return "", None, "Невідомо", ""

    name = chosen.get("name")
    clean_name = _clip(name) if isinstance(name, str) else ""
    offers = chosen.get("offers")
    price = _extract_price_value(_offer_field(offers, ("price", "lowPrice")))
    currency = _offer_field(offers, ("priceCurrency",))
    currency_text = currency.strip() if isinstance(currency, str) else ""
    availability = _offer_availability(offers)
    return clean_name, price, availability, currency_text


def _offer_field(offers: Any, keys: tuple[str, ...]) -> Any:
    if isinstance(offers, list):
        for offer in offers:
            value = _offer_field(offer, keys)
            if value not in (None, ""):
                return value
        return None
    if not isinstance(offers, dict):
        return None
    for key in keys:
        value = offers.get(key)
        if value not in (None, ""):
            return value
    spec = offers.get("priceSpecification")
    if spec is not None and spec is not offers:
        return _offer_field(spec, keys)
    return None


def _offer_availability(offers: Any) -> str:
    if isinstance(offers, list):
        statuses = [_offer_availability(item) for item in offers]
        for preferred in (
            "В наявності",
            "Закінчується",
            "Під замовлення",
            "Немає в наявності",
        ):
            if preferred in statuses:
                return preferred
        return "Невідомо"
    if not isinstance(offers, dict):
        return "Невідомо"
    raw = offers.get("availability") or offers.get("availabilityStatus")
    if isinstance(raw, str) and raw.strip():
        return _map_availability(raw)
    spec = offers.get("priceSpecification")
    if isinstance(spec, (dict, list)) and spec is not offers:
        mapped = _offer_availability(spec)
        if mapped != "Невідомо":
            return mapped
    return "Невідомо"


def _price_from_selectors(scope: BeautifulSoup, soup: BeautifulSoup) -> int | float | str | None:
    for root in _roots(scope, soup):
        for selector in PRICE_SELECTORS:
            node = root.select_one(selector)
            if node is None:
                continue
            value = _extract_price_value(_node_value(node, ("content", "data-price", "data-product-price", "value")))
            if not _is_blank_price(value):
                return value
    return None


def _availability_from_dom(scope: BeautifulSoup, soup: BeautifulSoup) -> str:
    for root in _roots(scope, soup):
        for selector in AVAILABILITY_SELECTORS:
            node = root.select_one(selector)
            if node is None:
                continue
            raw = _node_value(node, ("content", "href", "data-availability"))
            if raw:
                mapped = _map_availability(raw)
                if mapped != "Невідомо":
                    return mapped
    return "Невідомо"


def _map_availability(raw: str) -> str:
    value = clean_text(raw).lower()
    token = value.rstrip("/").rsplit("/", 1)[-1].replace("_", "").replace("-", "")
    if token in {"outofstock", "soldout", "discontinued"} or _contains_any(value, _OUT_OF_STOCK_PHRASES):
        return "Немає в наявності"
    if token in {"preorder", "backorder"} or _contains_any(value, _PREORDER_PHRASES):
        return "Під замовлення"
    if token == "limitedavailability" or _contains_any(value, _LIMITED_PHRASES):
        return "Закінчується"
    if token in {"instock", "instoreonly", "onlineonly"} or _contains_any(value, _IN_STOCK_PHRASES):
        return "В наявності"
    return "Невідомо"


def _extract_price_value(raw: object) -> int | float | str | None:
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, (int, float)):
        if raw == 0:
            return None
        return _finalize_number(float(raw))
    text = clean_text(str(raw))
    if not text:
        return None
    numbers = _find_price_numbers(text)
    lowered = text.lower()
    ranged = len(numbers) >= 2
    from_price = re.search(r"(?:^|\s)від\s+\d", lowered) is not None or re.search(
        r"\bfrom\s+\d", lowered
    )
    if ranged or from_price:
        if numbers or _has_currency_mark(lowered, text):
            return _ensure_uah_suffix(text)
        return None
    if not numbers or numbers[0] == 0:
        return None
    return numbers[0]


def _find_price_numbers(text: str) -> list[int | float]:
    numbers: list[int | float] = []
    for match in _PRICE_TOKEN_RE.finditer(text):
        number = _parse_numeric_token(match.group(0))
        if number is not None:
            numbers.append(number)
    return numbers


def _parse_numeric_token(token: str) -> int | float | None:
    compact = token.replace(" ", "").replace("\u00a0", "").replace("\u202f", "")
    if "," in compact and "." in compact:
        if compact.rfind(".") > compact.rfind(","):
            compact = compact.replace(",", "")
        else:
            compact = compact.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", compact):
        compact = compact.replace(".", "")
    elif "," in compact:
        compact = compact.replace(",", ".")
    if not re.fullmatch(r"\d+(\.\d+)?", compact):
        return None
    return _finalize_number(float(compact))


def _finalize_number(value: float) -> int | float:
    if value.is_integer():
        return int(value)
    return round(value, 2)


def _apply_currency(price: int | float | str, currency: str) -> int | float | str:
    code = currency.strip().upper()
    if code in {"", "UAH", "UA", "GRN", "ГРН", "₴"}:
        if isinstance(price, str):
            return _ensure_uah_suffix(price)
        return price
    return f"{price} {code}"


def _ensure_uah_suffix(text: str) -> str:
    if _has_currency_mark(text.lower(), text):
        return text
    return f"{text} грн"


def _has_currency_mark(lowered: str, original: str) -> bool:
    return "грн" in lowered or "uah" in lowered or "₴" in original


def _clip_price(price: int | float | str) -> int | float | str:
    if isinstance(price, str):
        return _clip(price, limit=200)
    return price


def _is_blank_price(price: object) -> bool:
    return price is None or price == ""


def _iter_nodes(data: Any) -> Any:
    if isinstance(data, list):
        for item in data:
            yield from _iter_nodes(item)
        return
    if not isinstance(data, dict):
        return
    graph = data.get("@graph")
    if graph is not None:
        yield from _iter_nodes(graph)
    yield data


def _has_type(node: dict[str, Any], expected: str) -> bool:
    raw = node.get("@type")
    if isinstance(raw, str):
        types = [raw]
    elif isinstance(raw, list):
        types = [str(item) for item in raw]
    else:
        return False
    expected_name = expected.lower()
    return any(_type_name(item) == expected_name for item in types)


def _type_name(value: str) -> str:
    return value.strip().rstrip("/").rsplit("/", 1)[-1].lower()


def _pick_scope(soup: BeautifulSoup) -> BeautifulSoup:
    for selector in ("[itemtype*='Product']", "main", "article", "#product", ".product"):
        node = soup.select_one(selector)
        if node is not None:
            return node
    return soup


def _roots(scope: BeautifulSoup, soup: BeautifulSoup) -> list[BeautifulSoup]:
    if scope is soup:
        return [soup]
    return [scope, soup]


def _heading(scope: BeautifulSoup, soup: BeautifulSoup) -> str:
    for root in _roots(scope, soup):
        node = root.find("h1")
        if node is None:
            continue
        text = clean_text(node.get_text(" ", strip=True))
        if text:
            return text
    return ""


def _meta_content(soup: BeautifulSoup, *, prop: str | None = None, name: str | None = None) -> str:
    if prop is not None:
        node = soup.find("meta", attrs={"property": prop})
    else:
        node = soup.find("meta", attrs={"name": name})
    if node is None:
        return ""
    return clean_text(str(node.get("content") or ""))


def _title_product_name(title: str) -> str:
    text = clean_text(title)
    parts = [
        part.strip()
        for part in re.split(r"\s+[|\-]\s+|\s+[\u2013\u2014]\s+", text)
        if part.strip()
    ]
    if len(parts) >= 2 and len(parts[0]) >= 3:
        return parts[0]
    return text


def _node_value(node: Any, attributes: tuple[str, ...]) -> str:
    for attribute in attributes:
        raw = node.get(attribute)
        if raw not in (None, ""):
            return str(raw).strip()
    return node.get_text(" ", strip=True)


def _contains_any(value: str, phrases: tuple[str, ...]) -> bool:
    return any(phrase in value for phrase in phrases)


def clean_text(value: str) -> str:
    text = value.replace("\u00a0", " ").replace("\u202f", " ")
    text = text.replace("\u2014", "-").replace("\u2013", "-").replace("\u2212", "-")
    return " ".join(text.split())


def _clip(value: str, limit: int = 500) -> str:
    text = clean_text(value)
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."
