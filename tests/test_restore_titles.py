import unittest

from omarchy_last_session.restore import titles


class TitleLikeness(unittest.TestCase):
    """Browsers append their own name to every title, and title a window
    Untitled, New Tab or about:blank until its page has loaded. Neither may
    count as likeness, or a window still loading pairs with whichever saved
    entry has the least in its title."""

    BYBIT = "▲ 78000.5 | Trade BTCUSDT | Bybit Perpetual Contracts - Brave"
    BLANK = "about:blank - Brave"

    def test_a_loading_title_scores_nothing_against_anything(self):
        for live in ("Untitled - Brave", "New Tab - Brave", "about:blank - Brave", "Brave"):
            with self.subTest(live=live):
                self.assertEqual(titles.score_title_likeness(self.BYBIT, live), 0.0)
                self.assertEqual(titles.score_title_likeness(self.BLANK, live), 0.0)

    def test_a_saved_placeholder_scores_nothing_too(self):
        self.assertEqual(titles.score_title_likeness(self.BLANK, "Example Domain - Brave"), 0.0)

    def test_the_app_name_does_not_count(self):
        """Two unrelated pages share ' - Brave'; the pages alone decide."""
        self.assertLess(titles.score_title_likeness("Home / X - Brave", "Example Domain - Brave"), 0.3)

    def test_a_drifted_page_title_still_matches(self):
        """Only the price moved: six of its seven words are still there."""
        drifted = "▼ 80697.5 | Trade BTCUSDT | Bybit Perpetual Contracts - Brave"
        self.assertGreater(titles.score_title_likeness(self.BYBIT, drifted), 0.8)

    def test_unrelated_pages_share_nothing(self):
        """Compared letter by letter these scored 0.22, above the 0.17 of the
        Bybit window's own early title, and the two windows swapped monitors
        (2026-09-20). Long titles share letters by chance, not words."""
        saved = "▲ 81099.6 | Trade BTCUSDT | Bybit Perpetual Contracts - Brave"
        for live in (
            "Junior Software Engineers | Hyphen | LinkedIn - Brave",
            "Brent oil - Price - Chart - Historical Data - News - Brave",
        ):
            with self.subTest(live=live):
                self.assertEqual(titles.score_title_likeness(saved, live), 0.0)

    def test_a_site_name_matches_the_full_title_it_is_part_of(self):
        """What these sites title a page until it has loaded, seen at restore."""
        for saved, live in (
            ("▲ 81099.6 | Trade BTCUSDT | Bybit Perpetual Contracts - Brave", "Bybit - Brave"),
            ("Inbox - someone@gmail.com - Gmail - Brave", "Gmail - Brave"),
            ('Wesker on X: "Something new" / X - Brave', "X - Brave"),
        ):
            with self.subTest(live=live):
                self.assertGreater(titles.score_title_likeness(saved, live), 0.0)

    def test_an_address_shown_while_loading_matches_its_page(self):
        """Its words come in another order than the page title's."""
        loading = "www.bybit.com/trade/usdt/BTCUSDT - Brave"
        self.assertGreater(titles.score_title_likeness(self.BYBIT, loading), 0.0)
        self.assertEqual(titles.score_title_likeness("Home / X - Brave", loading), 0.0)

    def test_a_number_still_tells_two_titles_apart(self):
        """The live suite's browser reopens 'tab 1' as 'tab 1 - reopened'."""
        live = "tab 1 - reopened"
        self.assertGreater(
            titles.score_title_likeness("tab 1", live), titles.score_title_likeness("tab 2", live)
        )

    def test_case_does_not_count(self):
        self.assertEqual(titles.score_title_likeness("GMAIL - Brave", "Gmail - Brave"), 1.0)

    def test_titles_without_an_app_name_compare_whole(self):
        self.assertEqual(titles.score_title_likeness("alex@host:~", "alex@host:~"), 1.0)
        self.assertEqual(titles.score_title_likeness("", "anything"), 0.0)

    def test_titles_with_no_words_compare_whole(self):
        self.assertEqual(titles.score_title_likeness("🎵 - Brave", "🎵 - Brave"), 1.0)
        self.assertEqual(titles.score_title_likeness("🎵 - Brave", "⚡ - Brave"), 0.0)

    def test_a_loading_title_is_recognised(self):
        for title in ("Untitled - Brave", "New Tab - Brave", "about:blank - Brave"):
            self.assertTrue(titles.is_still_loading(title), title)
        for title in ("Trade - Brave", "alex@host:~", "", "Mines"):
            self.assertFalse(titles.is_still_loading(title), title)


class TitleSettling(unittest.TestCase):
    """A browser titles a window before its page has loaded: blank, then the
    address or the site's name, then the page's own title. Rival windows are
    told apart once their titles stop changing, and a price or an unread count
    in a title never stops."""

    FULL = "▲ 84388.9 | Trade BTCUSDT | Bybit Perpetual Contracts - Brave"

    def test_an_unchanged_title_has_settled(self):
        for title in (self.FULL, "Mines", ""):
            with self.subTest(title=title):
                self.assertTrue(titles.has_title_settled(title, title))

    def test_a_moving_number_leaves_a_title_settled(self):
        for before, after in (
            (self.FULL, "▼ 84390.1 | Trade BTCUSDT | Bybit Perpetual Contracts - Brave"),
            ("(3) Inbox - Gmail - Brave", "(4) Inbox - Gmail - Brave"),
        ):
            with self.subTest(after=after):
                self.assertTrue(titles.has_title_settled(before, after))

    def test_a_page_replacing_its_early_title_has_not_settled(self):
        for early in ("Bybit - Brave", "www.bybit.com/trade/usdt/BTCUSDT - Brave"):
            with self.subTest(early=early):
                self.assertFalse(titles.has_title_settled(early, self.FULL))

    def test_a_placeholder_never_settles(self):
        for title in ("New Tab - Brave", "Untitled - Brave", "about:blank - Brave"):
            with self.subTest(title=title):
                self.assertFalse(titles.has_title_settled(title, title))
