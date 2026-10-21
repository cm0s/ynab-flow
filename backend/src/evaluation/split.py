"""Chronological train/evaluation split."""

from dataclasses import dataclass
from typing import List, Optional, Sequence

from src.evaluation.treatment import USABLE, Treated

DEFAULT_TRAIN_SHARE = 0.8


@dataclass
class Split:
    cutoff: str                  # first evaluation date (YYYY-MM-DD); training is strictly before
    training: List[Treated]
    evaluation: List[Treated]

    def ranges(self) -> dict:
        def span(items):
            dates = [t.row.date for t in items]
            return {"from": min(dates), "to": max(dates), "rows": len(items)} if dates else {"from": None, "to": None, "rows": 0}
        return {"cutoff": self.cutoff, "training": span(self.training), "evaluation": span(self.evaluation)}


def default_cutoff(treated: Sequence[Treated], share: float = DEFAULT_TRAIN_SHARE) -> str:
    """The date at which ``share`` of usable rows fall before it."""
    dates = sorted(t.row.date for t in treated if t.treatment in USABLE)
    if len(dates) < 2:
        raise ValueError("Not enough usable rows to split history.")
    cutoff = dates[min(len(dates) - 1, max(1, int(len(dates) * share)))]
    if cutoff == dates[0]:
        raise ValueError("All usable rows share one date; choose an explicit cut-off.")
    return cutoff


def split(treated: Sequence[Treated], cutoff: Optional[str] = None) -> Split:
    """Usable rows dated before ``cutoff`` train; usable rows on/after it are evaluated."""
    cut = cutoff or default_cutoff(treated)
    usable = [t for t in treated if t.treatment in USABLE]
    training = [t for t in usable if t.row.date < cut]
    evaluation = sorted((t for t in usable if t.row.date >= cut), key=lambda t: (t.row.date, t.row.line))
    if not training or not evaluation:
        raise ValueError(f"Cut-off {cut} leaves an empty training or evaluation period.")
    return Split(cut, sorted(training, key=lambda t: t.row.line), evaluation)
