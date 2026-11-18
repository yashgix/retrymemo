from collections import Counter

from .bm25 import bm25
from .data import Session, Task
from .text import session_text, words

MIN_HISTORY = 10
RRF_K = 60
STALE_DECAY = 0.8
RETRIEVERS = (
    "recent",
    "bm25_issue",
    "bm25_session",
    "bm25_paths",
    "hybrid",
    "resolved_only",
    "fresh",
)


class Corpus:
    def __init__(self, tasks: list[Task], sessions: list[Session]) -> None:
        self.tasks = tasks
        self.sessions = sessions
        self._task_index = {task.instance_id: i for i, task in enumerate(tasks)}
        self._repo_tasks: dict[str, list[int]] = {}
        for i, task in enumerate(tasks):
            self._repo_tasks.setdefault(task.repo, []).append(i)
        self._issue_docs = [Counter(words(task.issue)) for task in tasks]
        self._session_task: list[int] = []
        self._session_docs: list[Counter] = []
        self._path_docs: list[Counter] = []
        for session in sessions:
            task_index = self._task_index[session.instance_id]
            self._session_task.append(task_index)
            self._session_docs.append(Counter(words(session_text(tasks[task_index], session))))
            self._path_docs.append(Counter(words(" ".join(session.pointers))))

    def pool(self, q: int) -> list[int]:
        task = self.tasks[q]
        return [
            i
            for i, task_index in enumerate(self._session_task)
            if self.tasks[task_index].repo == task.repo and self.tasks[task_index].created_at < task.created_at
        ]

    def queries(self) -> list[int]:
        out = []
        for q, task in enumerate(self.tasks):
            earlier = sum(1 for j in self._repo_tasks[task.repo] if self.tasks[j].created_at < task.created_at)
            if earlier >= MIN_HISTORY:
                out.append(q)
        return out

    def later_fixes(self, q: int, i: int) -> int:
        pointers = set(self.sessions[i].pointers)
        if not pointers:
            return 0
        after = self.tasks[self._session_task[i]].created_at
        before = self.tasks[q].created_at
        return sum(
            1
            for j in self._repo_tasks[self.tasks[q].repo]
            if after < self.tasks[j].created_at < before and pointers.intersection(self.tasks[j].gold_files)
        )

    def most_files(self, q: int) -> list[int]:
        """Query-blind baseline: sessions that name more files first."""
        pool = self.pool(q)
        return self._order(pool, {i: len(self.sessions[i].pointers) for i in pool})

    def past_fix_files(self, q: int) -> list[str]:
        """No-session baseline: files changed by earlier fixes in the repo, most often first, then most recently."""
        query = self.tasks[q]
        count: dict[str, int] = {}
        last: dict[str, str] = {}
        for j in self._repo_tasks[query.repo]:
            task = self.tasks[j]
            if task.created_at < query.created_at:
                for path in task.gold_files:
                    count[path] = count.get(path, 0) + 1
                    last[path] = max(last.get(path, ""), task.created_at)
        return sorted(sorted(count), key=lambda path: (count[path], last[path]), reverse=True)

    def issue_tasks(self, q: int) -> list[int]:
        """Same-repo tasks created strictly before q, by BM25 of their issue against q's, newer first on ties."""
        query = self.tasks[q]
        earlier = [j for j in self._repo_tasks[query.repo] if self.tasks[j].created_at < query.created_at]
        scores = bm25(words(query.issue), [self._issue_docs[j] for j in earlier])
        score = dict(zip(earlier, scores, strict=True))
        return sorted(earlier, key=lambda j: (-score[j], -j))

    def _order(self, pool: list[int], scores: dict[int, float]) -> list[int]:
        return sorted(pool, key=lambda i: (-scores[i], -self._session_task[i], i))

    def _bm25_order(self, q: int, pool: list[int], docs: list[Counter]) -> list[int]:
        query = words(self.tasks[q].issue)
        scores = dict(zip(pool, bm25(query, [docs[i] for i in pool]), strict=True))
        return self._order(pool, scores)

    def _hybrid(self, q: int, pool: list[int]) -> tuple[list[int], dict[int, float]]:
        session_order = self._bm25_order(q, pool, self._session_docs)
        path_order = self._bm25_order(q, pool, self._path_docs)
        session_pos = {i: position for position, i in enumerate(session_order, 1)}
        path_pos = {i: position for position, i in enumerate(path_order, 1)}
        scores = {i: round(1 / (RRF_K + session_pos[i]) + 1 / (RRF_K + path_pos[i]), 9) for i in pool}
        return self._order(pool, scores), scores

    def rank(self, name: str, q: int) -> list[int]:
        if name not in RETRIEVERS:
            raise ValueError(f"unknown retriever: {name}")
        pool = self.pool(q)
        if name == "recent":
            return self._order(pool, dict.fromkeys(pool, 0.0))
        if name == "bm25_issue":
            query = words(self.tasks[q].issue)
            docs = [self._issue_docs[self._session_task[i]] for i in pool]
            scores = dict(zip(pool, bm25(query, docs), strict=True))
            return self._order(pool, scores)
        if name == "bm25_session":
            return self._bm25_order(q, pool, self._session_docs)
        if name == "bm25_paths":
            return self._bm25_order(q, pool, self._path_docs)
        if name == "hybrid":
            return self._hybrid(q, pool)[0]
        if name == "resolved_only":
            return [i for i in self._hybrid(q, pool)[0] if self.sessions[i].resolved]
        hybrid_scores = self._hybrid(q, pool)[1]
        scores = {i: round(hybrid_scores[i] * STALE_DECAY ** self.later_fixes(q, i), 9) for i in pool}
        return self._order(pool, scores)
