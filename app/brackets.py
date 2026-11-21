"""Tournament schedules and standings. Entrants are represented by registration IDs."""

from collections import defaultdict


def schedule(entrants, tournament_format):
    """List rounds in playing order. None represents a bye or a future winner."""
    players = list(entrants)
    if len(players) < 2:
        raise ValueError("Для сетки нужны как минимум два участника.")
    if tournament_format == "round_robin":
        if len(players) % 2:
            players.append(None)
        rounds = []
        for _ in range(len(players) - 1):
            pairs = zip(players[: len(players) // 2], reversed(players))
            rounds.append([(a, b) for a, b in pairs if a is not None and b is not None])
            players = [players[0], players[-1], *players[1:-1]]
        return rounds

    if tournament_format != "single_elimination":
        raise ValueError("Неизвестный формат турнира.")
    size = 1 << (len(players) - 1).bit_length()
    # Split byes across the draw. No opening match has two empty seats.
    seeds = [1, 2]
    while len(seeds) < size:
        complement = 2 * len(seeds) + 1
        seeds = [n for seed in seeds for n in (seed, complement - seed)]
    draw = [players[n - 1] if n <= len(players) else None for n in seeds]
    rounds = [list(zip(draw[::2], draw[1::2]))]
    while len(rounds[-1]) > 1:
        rounds.append([(None, None)] * (len(rounds[-1]) // 2))
    return rounds


def standings(entrants, matches, tournament_format):
    rows = {
        entry["id"]: {
            "registration_id": entry["id"],
            "name": entry["name"],
            "wins": 0,
            "draws": 0,
            "losses": 0,
            "played": 0,
            "rank": None,
        }
        for entry in entrants
    }
    eliminated = defaultdict(list)
    champion = None
    for match in matches:
        a, b = match["registration1_id"], match["registration2_id"]
        if match["status"] not in ("played", "walkover") or a is None or b is None:
            continue
        winner = match["winner_registration_id"]
        for player in (a, b):
            rows[player]["played"] += 1
            if winner is None:
                rows[player]["draws"] += 1
            elif winner == player:
                rows[player]["wins"] += 1
            else:
                rows[player]["losses"] += 1
                eliminated[match["round_number"]].append(player)
        if match["next_match_id"] is None:
            champion = winner

    if tournament_format == "round_robin":
        ordered = sorted(
            rows.values(), key=lambda row: (-row["wins"], row["name"].casefold(), row["registration_id"])
        )
        previous_wins = None
        for index, row in enumerate(ordered, 1):
            if row["wins"] != previous_wins:
                rank = index
                previous_wins = row["wins"]
            row["rank"] = rank
        return ordered

    if champion is None:
        return []
    rows[champion]["rank"] = 1
    ordered = [rows[champion]]
    for round_number in sorted(eliminated, reverse=True):
        place = len(ordered) + 1
        for player in eliminated[round_number]:
            rows[player]["rank"] = place
            ordered.append(rows[player])
    return ordered
