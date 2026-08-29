"""Deterministic normalization for monetary expressions in user messages."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import re


@dataclass(frozen=True)
class MonetaryExpression:
    """One normalized monetary-looking expression and its source span."""

    text: str
    amount: Decimal
    start: int
    end: int


class MonetaryExpressionParser:
    """Normalize common written forms without assigning a policy concept."""

    _numeric_pattern = re.compile(
        r"(?<![\w.])"
        r"(?P<prefix>£\s*|GBP\s+)?"
        r"(?P<number>"
        r"\d{1,3}(?:[,.\u00a0 ]\d{3})+(?:[,.]\d{1,2})?"
        r"|\d+(?:[,.]\d+)?"
        r")"
        r"(?:\s*(?P<scale>k|thousand|grand|m|million))?"
        r"(?:\s*(?P<suffix>GBP|pounds?))?"
        r"(?!\w)",
        re.IGNORECASE,
    )
    _number_word_pattern = re.compile(
        r"\b(?P<words>"
        r"(?:(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|"
        r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|"
        r"eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|"
        r"eighty|ninety|hundred|and)[\s-]+)*"
        r"(?:thousand|grand|million)"
        r")\b",
        re.IGNORECASE,
    )
    _small_numbers = {
        "zero": 0,
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
        "six": 6,
        "seven": 7,
        "eight": 8,
        "nine": 9,
        "ten": 10,
        "eleven": 11,
        "twelve": 12,
        "thirteen": 13,
        "fourteen": 14,
        "fifteen": 15,
        "sixteen": 16,
        "seventeen": 17,
        "eighteen": 18,
        "nineteen": 19,
        "twenty": 20,
        "thirty": 30,
        "forty": 40,
        "fifty": 50,
        "sixty": 60,
        "seventy": 70,
        "eighty": 80,
        "ninety": 90,
    }
    _scale_multipliers = {
        "k": Decimal("1000"),
        "thousand": Decimal("1000"),
        "grand": Decimal("1000"),
        "m": Decimal("1000000"),
        "million": Decimal("1000000"),
    }

    def extract(self, text: str) -> tuple[MonetaryExpression, ...]:
        expressions: list[MonetaryExpression] = []
        occupied: list[tuple[int, int]] = []
        for match in self._numeric_pattern.finditer(text):
            number = match.group("number")
            scale = (match.group("scale") or "").casefold()
            has_currency = bool(match.group("prefix") or match.group("suffix"))
            if not self._is_monetary_form(
                number=number,
                scale=scale,
                has_currency=has_currency,
            ):
                continue
            amount = self._parse_numeric(number)
            if scale:
                amount *= self._scale_multipliers[scale]
            expressions.append(
                MonetaryExpression(
                    text=match.group(0),
                    amount=amount,
                    start=match.start(),
                    end=match.end(),
                )
            )
            occupied.append((match.start(), match.end()))

        for match in self._number_word_pattern.finditer(text):
            if any(start < match.end() and match.start() < end for start, end in occupied):
                continue
            expressions.append(
                MonetaryExpression(
                    text=match.group(0),
                    amount=self._parse_number_words(match.group("words")),
                    start=match.start(),
                    end=match.end(),
                )
            )

        return tuple(sorted(expressions, key=lambda item: item.start))

    @staticmethod
    def _is_monetary_form(
        *,
        number: str,
        scale: str,
        has_currency: bool,
    ) -> bool:
        compact = number.replace(" ", "").replace("\u00a0", "")
        digits = compact.replace(",", "").replace(".", "")
        return (
            has_currency
            or bool(scale)
            or "," in compact
            or "." in compact
            or len(digits) >= 5
        )

    @staticmethod
    def _parse_numeric(value: str) -> Decimal:
        compact = value.replace(" ", "").replace("\u00a0", "")
        if "," in compact and "." in compact:
            decimal_separator = "," if compact.rfind(",") > compact.rfind(".") else "."
            grouping_separator = "." if decimal_separator == "," else ","
            return Decimal(
                compact.replace(grouping_separator, "").replace(
                    decimal_separator,
                    ".",
                )
            )
        separator = "," if "," in compact else "." if "." in compact else None
        if separator is None:
            return Decimal(compact)
        groups = compact.split(separator)
        if len(groups) > 1 and all(len(group) == 3 for group in groups[1:]):
            return Decimal("".join(groups))
        return Decimal(compact.replace(separator, "."))

    def _parse_number_words(self, value: str) -> Decimal:
        tokens = value.casefold().replace("-", " ").split()
        total = 0
        current = 0
        for token in tokens:
            if token == "and":
                continue
            if token in self._small_numbers:
                current += self._small_numbers[token]
            elif token == "hundred":
                current = max(current, 1) * 100
            elif token in {"thousand", "grand"}:
                total += max(current, 1) * 1_000
                current = 0
            elif token == "million":
                total += max(current, 1) * 1_000_000
                current = 0
        return Decimal(total + current)
