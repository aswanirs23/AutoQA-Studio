"""Deterministic input value generation by template name.

The LLM never types raw strings. It picks a template name from the
``TEMPLATES`` list and we materialize the actual value here. This eliminates
the "tested with aaa@aaa.com and called it a thorough email test" failure
mode and gives the orchestrator predictable mutation paths for negative-
testing forms.
"""

from __future__ import annotations

import random
import string

try:
    from faker import Faker
    _faker = Faker()
except ImportError:  # faker is in requirements.txt; this is a defensive import
    _faker = None


# Each template is a (category, generator) pair. The category drives mutation
# strategy in the orchestrator (e.g., "email" templates are tried on email
# fields; "any" templates work for any text field).
TEMPLATES: dict[str, str] = {
    "valid_email": "email",
    "invalid_email_no_at": "email",
    "invalid_email_no_domain": "email",
    "invalid_email_unicode": "email",
    "valid_password_strong": "password",
    "valid_password_weak": "password",
    "password_too_short": "password",
    "password_only_letters": "password",
    "valid_name": "name",
    "name_unicode": "name",
    "name_with_emoji": "name",
    "valid_phone_e164": "phone",
    "phone_with_letters": "phone",
    "valid_url_https": "url",
    "url_no_scheme": "url",
    "valid_integer_small": "number",
    "valid_integer_large": "number",
    "negative_integer": "number",
    "zero": "number",
    "decimal_with_many_digits": "number",
    "non_numeric_in_number_field": "number",
    "empty": "any",
    "single_space": "any",
    "very_long_1000_chars": "any",
    "sql_injection_pattern": "any",
    "xss_script_tag": "any",
    "unicode_rtl": "any",
    "control_chars": "any",
    "leading_trailing_whitespace": "any",
}


def category_of(template: str) -> str:
    """Return the category ('email' / 'password' / 'name' / 'any' / ...)."""
    return TEMPLATES.get(template, "any")


def generate(template: str, *, seed: int | None = None) -> str:
    """Materialize a template into a concrete string value.

    Deterministic when ``seed`` is provided (so re-runs of an exploration
    produce the same form-input values, which makes ledger entries
    reproducible).
    """
    rng = random.Random(seed)
    f = _faker

    if template == "valid_email":
        return f.email() if f else f"user{rng.randint(100, 9999)}@example.com"
    if template == "invalid_email_no_at":
        return "not-an-email-string"
    if template == "invalid_email_no_domain":
        return "user@"
    if template == "invalid_email_unicode":
        return "ユーザー@例え.テスト"
    if template == "valid_password_strong":
        return "Tg9!" + (f.password(length=12) if f else "Aa1!Aa1!Aa1!")
    if template == "valid_password_weak":
        return "password"
    if template == "password_too_short":
        return "ab1"
    if template == "password_only_letters":
        return "abcdefghijkl"
    if template == "valid_name":
        return f.name() if f else "Test User"
    if template == "name_unicode":
        return "测试用户 Müller"
    if template == "name_with_emoji":
        return "Test 🚀 User"
    if template == "valid_phone_e164":
        return "+15555550123"
    if template == "phone_with_letters":
        return "555-CALL-NOW"
    if template == "valid_url_https":
        return "https://example.com/path"
    if template == "url_no_scheme":
        return "example.com"
    if template == "valid_integer_small":
        return str(rng.randint(1, 99))
    if template == "valid_integer_large":
        return "999999999"
    if template == "negative_integer":
        return "-42"
    if template == "zero":
        return "0"
    if template == "decimal_with_many_digits":
        return "3.14159265358979"
    if template == "non_numeric_in_number_field":
        return "abc"
    if template == "empty":
        return ""
    if template == "single_space":
        return " "
    if template == "very_long_1000_chars":
        return "a" * 1000
    if template == "sql_injection_pattern":
        return "'; DROP TABLE users; --"
    if template == "xss_script_tag":
        return "<script>alert(1)</script>"
    if template == "unicode_rtl":
        return "مرحبا بالعالم"
    if template == "control_chars":
        return "abc\x00def\x07ghi"
    if template == "leading_trailing_whitespace":
        return "   trimmed   "

    # Unknown template — fall back to a random-but-bounded string so we never
    # return None.
    return "".join(rng.choices(string.ascii_letters, k=8))


def list_templates_for_category(category: str) -> list[str]:
    """Return all templates whose category matches (or is 'any')."""
    return [t for t, c in TEMPLATES.items() if c == category or c == "any"]
