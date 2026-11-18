"""The v2 retriever: ranked voters (sessions or past fixes) vote for files, fused with the past-fix prior."""

from dataclasses import dataclass
from typing import Protocol


class Voter(Protocol):
    """Anything that names files and marks some as edited: a session, or a past fix."""

    @property
    def pointers(self) -> tuple[str, ...]: ...

    @property
    def edited(self) -> tuple[str, ...]: ...


@dataclass(frozen=True)
class Fix:
    """A past task's merged fix as a voter: it names and edits its gold files."""

    pointers: tuple[str, ...]

    @property
    def edited(self) -> tuple[str, ...]:
        return self.pointers


@dataclass(frozen=True)
class VoteSettings:
    depth: int  # how many top hybrid sessions vote
    cap: int | None  # a session naming more files than this has its vote spread thinner
    edited_weight: float  # an edited file's vote relative to an opened file's
    prior_weight: float  # share of the past-fix prior in the final fusion
    fuse_k: int  # reciprocal rank constant of the final fusion


# Chosen on the development half only by tools/tune_vote.py (results/tuning.json), then frozen.
VOTE = VoteSettings(depth=5, cap=None, edited_weight=1.0, prior_weight=0.5, fuse_k=60)


def _ranks(scores: dict[str, float]) -> dict[str, int]:
    ordered = sorted(sorted(scores), key=lambda path: scores[path], reverse=True)
    return {path: position for position, path in enumerate(ordered, 1)}


def votes(ranked: list[Voter], settings: VoteSettings) -> dict[str, float]:
    out: dict[str, float] = {}
    for position, voter in enumerate(ranked[: settings.depth], 1):
        pointers = voter.pointers
        if not pointers:
            continue
        spread = 1.0 if settings.cap is None else min(1.0, settings.cap / len(pointers))
        for path in pointers:
            weight = settings.edited_weight if path in voter.edited else 1.0
            out[path] = out.get(path, 0.0) + spread * weight / (settings.fuse_k + position)
    return out


def vote_files(ranked: list[Voter], prior: list[str], settings: VoteSettings = VOTE) -> list[str]:
    """`ranked` are the voters in rank order (hybrid sessions, or past fixes), `prior` the past-fix list, best first."""
    vote_rank = _ranks(votes(ranked, settings))
    prior_rank = {path: position for position, path in enumerate(prior, 1)}
    k = settings.fuse_k
    score = {
        path: (1 - settings.prior_weight) / (k + vote_rank[path]) if path in vote_rank else 0.0
        for path in vote_rank.keys() | prior_rank.keys()
    }
    for path, position in prior_rank.items():
        score[path] += settings.prior_weight / (k + position)
    return list(_ranks(score))
