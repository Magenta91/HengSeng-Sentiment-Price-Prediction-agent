"""Ticker relevance validation to prevent false-positive ticker assignments.

Example issues:
- TVB (0511.HK) partnerships with Tencent (0700.HK) assigned to Tencent stock discussion
- General corporate news (partnerships, lawsuits) vs investment discussion
- Company mentions in unrelated contexts

Rules:
- If confidence < 0.7 and sentiment is neutral, treat as ambiguous (not relevant)
- If text mentions multiple tickers but focuses on a different one, reduce confidence
- If text is about partnerships/collaborations, check if it's about the stock itself
"""

import re
from typing import Pattern


# Known confusion patterns
TICKER_CONFUSION_PATTERNS: dict[str, list[tuple[Pattern, str]]] = {
    "0700.HK": [  # Tencent Holdings
        (re.compile(r"TVB.*?騰訊|騰訊.*?TVB", re.IGNORECASE), "TVB partnership - may not be about Tencent stock"),
        (re.compile(r"0511|511.*?騰訊|騰訊.*?0511|511", re.IGNORECASE), "Mentions TVB (0511.HK)"),
    ],
    "0511.HK": [  # TVB
        (re.compile(r"騰訊.*?TVB|TVB.*?騰訊", re.IGNORECASE), "Tencent partnership - may not be about TVB stock"),
    ],
}


def check_ticker_relevance(
    text: str,
    symbol: str,
    sentiment: str,
    confidence: float,
    direction: str
) -> tuple[bool, list[str]]:
    """Check if extraction is actually relevant to the ticker.
    
    Returns:
        (is_relevant, notes): is_relevant=False means extraction should be filtered out
    """
    notes = []
    
    # If confidence is already low and sentiment is neutral/unclear, likely not relevant
    if confidence < 0.7 and sentiment == "neutral" and direction == "unclear":
        notes.append("Low confidence + neutral sentiment suggests tangential mention only")
        return False, notes
    
    # Check for known confusion patterns
    if symbol in TICKER_CONFUSION_PATTERNS:
        for pattern, note in TICKER_CONFUSION_PATTERNS[symbol]:
            if pattern.search(text):
                notes.append(note)
                
                # If it's a partnership/collaboration context with neutral sentiment, filter out
                if sentiment == "neutral":
                    notes.append("Partnership context with neutral sentiment - not actionable for ticker")
                    return False, notes
                
                # Otherwise, reduce confidence but keep
                if confidence > 0.6:
                    notes.append(f"Reduced confidence from {confidence:.2f} to 0.5 due to potential confusion")
                    # Caller should update confidence
    
    return True, notes


def apply_ticker_relevance_filter(extraction_data: dict, source_text: str) -> tuple[dict | None, list[str]]:
    """Apply ticker relevance filtering to extraction data.
    
    Returns:
        (filtered_data, notes): filtered_data=None means extraction should be discarded
    """
    symbol = extraction_data.get("asset_symbol")
    sentiment = extraction_data.get("sentiment")
    confidence = extraction_data.get("confidence", 1.0)
    direction = extraction_data.get("direction")
    
    is_relevant, notes = check_ticker_relevance(source_text, symbol, sentiment, confidence, direction)
    
    if not is_relevant:
        return None, notes
    
    # If relevant but with notes, return modified data
    if notes:
        out = dict(extraction_data)
        # Reduce confidence for ambiguous cases
        if any("reduced confidence" in n.lower() for n in notes):
            out["confidence"] = 0.5
        return out, notes
    
    return extraction_data, []
