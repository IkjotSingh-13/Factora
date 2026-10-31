"""Test suite for VerifyAI S1 — Claim Extraction."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def solar_article() -> str:
    """The atomicity example from the specification (+ extra sentences)."""
    return (
        "India launched 15 solar projects in 2025. "
        "These projects have a combined capacity of 10 GW. "
        "The government announced an investment of Rs 5,000 crore for them. "
        "India may become the world's largest solar producer by 2030. "
        "The country currently has the most impressive renewable energy sector in Asia. "
        "India installed 25 GW of solar capacity in 2025."
    )


@pytest.fixture
def stats_article() -> str:
    return (
        "Apple reported revenue of $94.9 billion for the quarter ending 28 September 2024. "
        "The company sold 46.2 million iPhones during the period. "
        "Apple's services revenue grew 12% year over year. "
        "Analysts expected revenue of about $94 billion."
    )


@pytest.fixture
def announcement_article() -> str:
    return (
        "The Ministry of New and Renewable Energy announced on Tuesday that it will "
        "build 100 solar parks across India by 2027. "
        "The ministry allocated Rs 12,000 crore for the programme. "
        "Officials said the first 20 parks would be commissioned next year."
    )


@pytest.fixture
def sample_html() -> str:
    body = (
        "India installed a record 25 GW of solar capacity in 2025, according to the "
        "Ministry of New and Renewable Energy. The ministry said the country's total "
        "renewable capacity now stands at 210 GW. Solar accounted for 18 GW of the "
        "additions while wind contributed 5 GW. The government announced a further "
        "Rs 8,000 crore for rooftop solar in the next financial year."
    )
    return f"""
    <html><head><title>Solar record</title>
    <style>.ad {{ color: red; }}</style></head>
    <body>
      <nav><a href="/">Home</a><a href="/solar">Solar</a></nav>
      <div class="advert">Buy cheap solar panels now! Click here.</div>
      <article>
        <h1>India installs record solar capacity</h1>
        <p>{body}</p>
      </article>
      <aside class="sidebar"><p>Related: ten other stories you must read today.</p></aside>
      <footer>Copyright 2026 Example News. All rights reserved.</footer>
      <script>window.dataLayer = [];</script>
    </body></html>
    """
