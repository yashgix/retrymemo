"""v3 (SPEC-v3.md): sessions as a search index into past fixes, against the strongest no-session control."""

from collections import Counter

from .bm25 import bm25
from .retrieve import RRF_K, Corpus
from .text import words
from .vote import Fix, vote_files

RANKERS = ("session_index_fix", "fix_hybrid", "vote_prior", "issue_fix_vote", "past_fix_files")
COMPARISONS = (
    ("session_index_fix", "fix_hybrid"),
    ("vote_prior", "fix_hybrid"),
    ("vote_prior", "issue_fix_vote"),
    ("fix_hybrid", "issue_fix_vote"),
)


class History:
    """Same-repo tasks created strictly before a query, with their merged fixes as voters."""

    def __init__(self, corpus: Corpus) -> None:
        self.corpus = corpus
        self._path_docs = [Counter(words(" ".join(task.gold_files))) for task in corpus.tasks]

    def earlier(self, q: int) -> list[int]:
        tasks = self.corpus.tasks
        return [
            j for j, task in enumerate(tasks) if task.repo == tasks[q].repo and task.created_at < tasks[q].created_at
        ]

    def fix_hybrid_tasks(self, q: int) -> list[int]:
        """Earlier tasks by reciprocal rank fusion of BM25 on their issue and on their fix's file paths."""
        earlier = self.earlier(q)
        query = words(self.corpus.tasks[q].issue)
        fused = dict.fromkeys(earlier, 0.0)
        for docs in (self.corpus._issue_docs, self._path_docs):
            scores = dict(zip(earlier, bm25(query, [docs[j] for j in earlier]), strict=True))
            for position, j in enumerate(sorted(earlier, key=lambda j: (-scores[j], -j)), 1):
                fused[j] += 1 / (RRF_K + position)
        return sorted(earlier, key=lambda j: (-round(fused[j], 9), -j))

    def session_index_tasks(self, q: int) -> list[int]:
        """Earlier tasks in the order their first session appears in v1's hybrid session ranking."""
        session_task = self.corpus._session_task
        return list(dict.fromkeys(session_task[i] for i in self.corpus.rank("hybrid", q)))

    def file_lists(self, q: int) -> dict[str, list[str]]:
        corpus = self.corpus
        prior = corpus.past_fix_files(q)

        def fix_vote(order: list[int]) -> list[str]:
            return vote_files([Fix(corpus.tasks[j].gold_files) for j in order], prior)

        return {
            "session_index_fix": fix_vote(self.session_index_tasks(q)),
            "fix_hybrid": fix_vote(self.fix_hybrid_tasks(q)),
            "vote_prior": vote_files([corpus.sessions[i] for i in corpus.rank("hybrid", q)], prior),
            "issue_fix_vote": fix_vote(corpus.issue_tasks(q)),
            "past_fix_files": prior,
        }
