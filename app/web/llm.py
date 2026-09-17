"""DeepSeek summary of scraped results (stdlib urllib only).

The API key is loaded from credentials/deepseek.env (chmod 600, gitignored)
by the caller — it is not embedded in code and never appears in logs. The
summary text itself is stored in runs.summary (shown in the web UI only; the
Google Sheets writer never receives it).
"""

import json
import urllib.error
import urllib.request

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-chat"
MAX_ROWS = 150
TIMEOUT_SECONDS = 120


class LLMError(Exception):
    """Friendly, user-facing LLM error (never contains the API key)."""


SYSTEM_PROMPT = (
    "You are a concise analyst summarizing Indian GeM (Government e-Marketplace) "
    "tenders for a metals trading business. Group bids by organization, call out "
    "the soonest-closing and the largest-quantity bids, and note any unusual items. "
    "Plain text, short paragraphs, no markdown tables, and never invent facts — "
    "work only from the rows you are given."
)


def summarize(result_rows, api_key):
    """result_rows: DB rows with bid_number/end_date_ist/end_time_ist/organization/item/quantity.

    Raises LLMError with a friendly message on any failure.
    """
    if not result_rows:
        raise LLMError("No results to summarize.")

    truncated = len(result_rows) > MAX_ROWS
    lines = []
    for r in result_rows[:MAX_ROWS]:
        lines.append(
            "Bid %s | ends %s %s IST | %s | %s | qty %s" % (
                r["bid_number"] or "-",
                r["end_date_ist"] or "-",
                r["end_time_ist"] or "-",
                r["organization"] or "-",
                r["item"] or "-",
                r["quantity"] or "-",
            )
        )
    note = (
        " (showing the first %d of %d results — mention this in the summary)"
        % (MAX_ROWS, len(result_rows))
    ) if truncated else ""
    user_prompt = (
        "Here are %d GeM tender bids%s:\n\n%s\n\nSummarize for the trading desk."
    ) % (len(result_rows), note, "\n".join(lines))

    body = json.dumps({
        "model": MODEL,
        "temperature": 0,
        "max_tokens": 1500,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    }).encode("utf-8")
    req = urllib.request.Request(
        DEEPSEEK_URL,
        data=body,
        headers={
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as err:
        raise LLMError(_map_http_error(err.code))
    except urllib.error.URLError:
        raise LLMError("Could not reach DeepSeek — network problem or timeout.")
    except json.JSONDecodeError:
        raise LLMError("DeepSeek returned an unreadable response.")

    try:
        return data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError):
        raise LLMError("DeepSeek response was missing content.")


def _map_http_error(code):
    if code == 401:
        return "The API key was rejected (401). Check the key and try again."
    if code in (402, 429):
        return "DeepSeek returned HTTP %d — billing or quota problem on this key." % code
    return "DeepSeek returned HTTP %d." % code
