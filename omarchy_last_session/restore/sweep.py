"""The sweep: watches the windows that turn up after launch, then pairs and places them."""

from __future__ import annotations

import dataclasses
import time

from omarchy_last_session import config, hypr, log
from omarchy_last_session.restore import pairing, placement, titles
from omarchy_last_session.session import SavedWindow


def sweep(
    pending: list[SavedWindow], seen: set[str], origins: hypr.Origins
) -> tuple[list[pairing.Pairing], list[SavedWindow]]:
    """Place the windows that escaped their exec_cmd rule, which tracks the
    spawned pid and so misses forking and single-instance apps. Returns the
    (entry, address) pairs placed and the entries that never got a window."""
    pending = list(pending)
    placed: list[pairing.Pairing] = []
    watched: dict[str, Sighting] = {}
    deadline = time.time() + config.SWEEP_TIMEOUT
    while pending:
        time.sleep(1)
        now = time.time()
        if now >= deadline:
            break
        placed += place_new_arrivals(pending, seen, origins, watched, now, deadline)
    return placed, pending


@dataclasses.dataclass
class Sighting:
    """A window the sweep has yet to pair: when it turned up, its title at the
    last pass, and whether that title had stopped changing by then."""

    first_seen: float
    title: str
    is_settled: bool = False


def place_new_arrivals(
    pending: list[SavedWindow],
    seen: set[str],
    origins: hypr.Origins,
    watched: dict[str, Sighting],
    now: float,
    deadline: float,
) -> list[pairing.Pairing]:
    """One pass over the desktop. Updates pending, seen and watched in place."""
    in_play = {
        address: client
        for address, client in hypr.get_managed_clients().items()
        if address not in seen and pairing.find_candidates(pending, client)
    }
    watch_titles(in_play, watched, now)
    ready = find_ready_windows(pending, in_play, watched, now, deadline)
    placed = pairing.pair_arrivals(pending, ready)
    for entry, address in placed:
        pending.remove(entry)
        seen.add(address)
        place_arrival(entry, address, ready[address], origins)
    return placed


def watch_titles(in_play: dict[str, hypr.Client], watched: dict[str, Sighting], now: float) -> None:
    """Logs the title each window turns up with and every change after it: a
    browser titles a window long before its page has loaded."""
    for address, client in in_play.items():
        cls, title, before = client.get("class", ""), client.get("title", ""), watched.get(address)
        if before is None:
            log(f"{cls} {address} turned up on workspace {client['workspace'].get('id')} titled '{title}'")
            watched[address] = Sighting(now, title)
            continue
        if title != before.title:
            log(f"{cls} {address} is now titled '{title}'")
        watched[address] = Sighting(before.first_seen, title, titles.has_title_settled(before.title, title))


def find_ready_windows(
    pending: list[SavedWindow],
    in_play: dict[str, hypr.Client],
    watched: dict[str, Sighting],
    now: float,
    deadline: float,
) -> dict[str, hypr.Client]:
    """The windows to pair this pass: all of a class together, once titles can
    tell them apart or TITLE_SETTLE has passed since the last of them turned up."""
    ready: dict[str, hypr.Client] = {}
    for cls, rivals in group_by_class(in_play).items():
        wanted = len(
            {index for client in rivals.values() for index in pairing.find_candidates(pending, client)}
        )
        unsettled = sum(not watched[address].is_settled for address in rivals)
        if can_tell_apart(len(rivals), wanted, unsettled):
            ready.update(rivals)
            continue
        waited = now - max(watched[address].first_seen for address in rivals)
        if waited >= config.TITLE_SETTLE or now >= deadline - 1:
            log(
                f"waited {waited:.0f} s for {cls} windows: {len(rivals)} of {wanted} turned up,"
                f" {unsettled} still changing titles"
            )
            ready.update(rivals)
    return ready


def can_tell_apart(windows: int, entries: int, unsettled: int) -> bool:
    """Once every rival has turned up and its title has settled. A lone window
    with one entry to take has nothing to be told apart from."""
    return (windows == 1 and entries == 1) or (windows >= entries and unsettled == 0)


def group_by_class(clients: dict[str, hypr.Client]) -> dict[str, dict[str, hypr.Client]]:
    classes: dict[str, dict[str, hypr.Client]] = {}
    for address, client in clients.items():
        classes.setdefault(client.get("class", ""), {})[address] = client
    return classes


def place_arrival(entry: SavedWindow, address: str, client: hypr.Client, origins: hypr.Origins) -> None:
    # Moving one member of a group moves the whole group; a group of one
    # is still just a window.
    fixing = len(client.get("grouped") or []) <= 1 and placement.is_out_of_place(entry, client, origins)
    log(describe_pairing(entry, address, client, fixing))
    if fixing:
        placement.place_window(entry, address, origins, floating=bool(client.get("floating")))


def describe_pairing(entry: SavedWindow, address: str, client: hypr.Client, fixing: bool) -> str:
    command, title, _ = pairing.score_fit(entry, client, pairing.get_client_argv(client))
    return (
        f"{client.get('class', '')} {address} '{client.get('title', '')}'"
        f" on workspace {client['workspace'].get('id')}"
        f" is the saved '{entry.get('title', '')}' from workspace {entry['workspace'].get('id')}"
        f" (command {command}, title {title:.2f}); {'placing it' if fixing else 'already in place'}"
    )
