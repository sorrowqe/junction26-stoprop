"""syndication receipt: exact-phrase search for copies (stretch goal; needs SEARCH_PROVIDER).

Not implemented in v1: always reported honestly as "not checked".
"""


def not_checked() -> dict:
    return {"status": "not_checked", "flag": "neutral", "evidence_url": None,
            "text": "Search for copies of this text on other sites is not enabled yet, so it was not checked."}
