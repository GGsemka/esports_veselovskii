import json

import pytest
from test_workflow import card_titles

from app import db
from app.cli import ARCHIVED_DEMO_TOURNAMENTS, seed, seed_featured_tournaments


@pytest.mark.fr("FR-007", "FR-018", "FR-019", "FR-022")
def test_standard_demo_archive_lists_results_and_filters(app, client):
    result = app.test_cli_runner().invoke(args=["seed-demo", "--password", "Test-demo-only"])
    assert result.exit_code == 0, result.exception

    with app.app_context():
        rows = db.all(
            """SELECT id,title,game_id,completed_at,results_snapshot FROM tournaments
               WHERE status='completed' ORDER BY completed_at DESC,id DESC"""
        )
        dates = {title: day for _, title, day, _, _, _ in ARCHIVED_DEMO_TOURNAMENTS}
        assert len(rows) == 7
        assert {row["title"] for row in rows} == {*dates, "Осенний финал"}

        first = client.get("/history")
        second = client.get("/history?page=2")
        assert card_titles(first) == [row["title"] for row in rows[:6]]
        assert card_titles(second) == [rows[-1]["title"]]

        for row in rows:
            detail = client.get(f"/tournaments/{row['id']}")
            assert detail.status_code == 200
            html = detail.get_data(as_text=True)
            assert "Итоговые места" in html
            snapshot = json.loads(row["results_snapshot"])
            played = [match for group in snapshot["rounds"] for match in group]
            assert len(played) == (3 if row["title"] in dates else 1)
            assert all(match["status"] == "played" and match["name1"] and match["name2"] for match in played)
            assert all(
                f'<strong class="mono">{match[f"score{side}"]}</strong>' in html
                for match in played for side in (1, 2)
            )
            assert snapshot["standings"][0]["rank"] == 1
            assert all(entry["name"] in html and entry["rank"] for entry in snapshot["standings"])
            if row["title"] in dates:
                assert row["completed_at"].date().isoformat() == dates[row["title"]]
        assert len({row["game_id"] for row in rows if row["title"] in dates}) == 6

        selected = rows[-1]["game_id"]
        assert card_titles(client.get(f"/history?game={selected}")) == [
            row["title"] for row in rows if row["game_id"] == selected
        ]


@pytest.mark.fr("FR-007", "FR-022")
def test_demo_archive_backfill_is_idempotent_and_preserves_existing_data(app):
    runner = app.test_cli_runner()
    with app.app_context():
        seed("Test-demo-only")
        seed_featured_tournaments()

        def saved_tournaments():
            return db.all(
                "SELECT id,title,status,completed_at,results_snapshot FROM tournaments ORDER BY id"
            )

        previous = saved_tournaments()
        assert len(previous) == 10
        first = runner.invoke(args=["seed-demo-history"])
        assert first.exit_code == 0, first.exception
        assert "Добавлено турниров в историю: 6." in first.output
        expanded = saved_tournaments()
        assert expanded[:len(previous)] == previous
        assert len(expanded) == 16
        counts = {
            table: db.one(f"SELECT COUNT(*) AS total FROM {table}")["total"]
            for table in ("matches", "registrations", "teams", "team_members", "users")
        }

        second = runner.invoke(args=["seed-demo-history"])
        assert second.exit_code == 0, second.exception
        assert "Добавлено турниров в историю: 0." in second.output
        assert saved_tournaments() == expanded
        for table, count in counts.items():
            assert db.one(f"SELECT COUNT(*) AS total FROM {table}")["total"] == count
