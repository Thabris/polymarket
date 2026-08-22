"""Deadline-family discovery tests."""

from datetime import timedelta

from core.models import Market
from core.timeutil import utcnow
from scanners.calendar_families import (
    description_similarity,
    discover_families,
    parse_deadline_stem,
)


def _market(question, end_days, **kwargs) -> Market:
    base = {
        "id": kwargs.pop("id", question[:20]),
        "condition_id": "0x1",
        "question": question,
        "end_date": utcnow() + timedelta(days=end_days),
    }
    base.update(kwargs)
    return Market(**base)


class TestStemParsing:
    def test_by_month_day(self):
        parsed = parse_deadline_stem("Strait of Hormuz traffic returns to normal by August 31?")
        assert parsed is not None
        stem, phrase, direction = parsed
        assert stem == "strait of hormuz traffic returns to normal"
        assert phrase.lower().startswith("by august")
        assert direction == "up"

    def test_survival_direction(self):
        parsed = parse_deadline_stem("US ceasefire against Iran continues through August 31?")
        assert parsed is not None
        assert parsed[2] == "down"  # survival: P decreases with later deadline

    def test_same_stem_different_deadlines(self):
        a = parse_deadline_stem("Strait of Hormuz traffic returns to normal by August 31?")
        b = parse_deadline_stem("Strait of Hormuz traffic returns to normal by September 30?")
        assert a[0] == b[0]

    def test_before_year(self):
        parsed = parse_deadline_stem("Xi Jinping out before 2027?")
        assert parsed is not None
        assert parsed[0] == "xi jinping out"

    def test_by_full_date(self):
        parsed = parse_deadline_stem("US announces end of Iranian blockade by August 22, 2026?")
        assert parsed is not None

    def test_window_market_excluded(self):
        # "in September" = decision-at-meeting window, NOT cumulative — the
        # monotonicity constraint does not apply to windows
        assert parse_deadline_stem("Fed Decision in September?") is None
        assert parse_deadline_stem("Will XRP reach $2.00 in August?") is None

    def test_non_deadline_excluded(self):
        assert parse_deadline_stem("Will AC Monza win on 2026-08-22?") is None
        assert parse_deadline_stem("Counter-Strike: FURIA vs FUT Esports (BO3)") is None


class TestDiscovery:
    def test_groups_tranches(self):
        markets = [
            _market("X happens by August 31?", 9, id="near"),
            _market("X happens by September 30?", 39, id="far"),
            _market("Unrelated thing by August 31?", 9, id="other"),
        ]
        families = discover_families(markets)
        assert len(families) == 1
        members = next(iter(families.values()))
        assert [m.id for m in members] == ["near", "far"]  # deadline-sorted

    def test_negrisk_buckets_excluded(self):
        # outcome buckets share one deadline -> not a deadline family
        markets = [
            _market("Fed decision by September 17?", 26, id="a"),
            _market("Fed decision by September 17?", 26, id="b"),
        ]
        assert discover_families(markets) == {}


class TestSimilarity:
    def test_identical_high(self):
        near = _market("X by August 31?", 9, description="Resolves YES per official OPM data at 11:59 PM ET.")
        far = _market("X by September 30?", 39, description="Resolves YES per official OPM data at 11:59 PM ET.")
        grade, _ = description_similarity(near, far)
        assert grade == "high"

    def test_different_sources_mismatch(self):
        near = _market("X by August 31?", 9, resolution_source="https://opm.gov",
                       description="Resolves per OPM.")
        far = _market("X by September 30?", 39, resolution_source="https://apnews.com",
                      description="Resolves per AP.")
        grade, details = description_similarity(near, far)
        assert grade == "MISMATCH"
        assert "resolution_sources" in details

    def test_diverged_text_mismatch(self):
        near = _market("X by August 31?", 9,
                       description="Resolves YES if the 8-K filing is published by the deadline per SEC EDGAR.")
        far = _market("X by September 30?", 39,
                      description="Resolves YES based on consensus of credible reporting about the transaction date.")
        grade, _ = description_similarity(near, far)
        assert grade == "MISMATCH"
