"""Titles compared by the words they share, and when a loading page's title has settled."""

import re

TITLE_SEPARATORS = (" - ", " \u2014 ")
# What a browser titles a window until its page has loaded.
PLACEHOLDER_PAGES = frozenset(("untitled", "new tab", "about:blank"))
# Titles are compared by the words they share: two unrelated long titles have
# more letters in common by chance than a page's early title has with its own.
WORD = re.compile(r"\w+")


def score_title_likeness(saved, live):
    """The share of words two titles have in common, without the app name both end
    in. A placeholder or missing title scores zero rather than match on the app name."""
    saved_page, live_page = strip_shared_app_name(saved, live)
    if not saved_page or not live_page or is_placeholder(saved_page) or is_placeholder(live_page):
        return 0.0
    saved_words, live_words = get_words(saved_page), get_words(live_page)
    if not saved_words or not live_words:
        return float(saved_page == live_page)
    return 2 * len(saved_words & live_words) / (len(saved_words) + len(live_words))


def has_title_settled(before, after):
    """The same words a pass apart, numbers aside, and no placeholder: a price or
    an unread count keeps moving on a page that has loaded."""
    return not is_still_loading(after) and drop_numbers(get_words(before)) == drop_numbers(get_words(after))


def get_words(text):
    return set(WORD.findall(text.lower()))


def drop_numbers(words):
    return {word for word in words if not word.isdigit()}


def strip_shared_app_name(saved, live):
    saved_page, saved_app = split_title(saved)
    live_page, live_app = split_title(live)
    if saved_app and live_app == saved_app:
        return saved_page, live_page
    if saved_app and live.strip() == saved_app:
        return saved_page, ""
    return saved.strip(), live.strip()


def split_title(title):
    """('page', 'App') for 'page - App', else the whole title and no app."""
    for separator in TITLE_SEPARATORS:
        page, found, app = title.rpartition(separator)
        if found:
            return page.strip(), app.strip()
    return title.strip(), ""


def is_placeholder(page):
    return page.lower() in PLACEHOLDER_PAGES


def is_still_loading(title):
    return is_placeholder(split_title(title)[0])
