"""The v2 date split: development and test halves of the queries."""


def date_split(tasks, queries: list[int]) -> tuple[list[int], list[int]]:
    """Per repository, the earlier half of its queries by `created_at` is development, the later half is test."""
    by_repo: dict[str, list[int]] = {}
    for q in queries:
        by_repo.setdefault(tasks[q].repo, []).append(q)
    dev, test = [], []
    for repo_queries in by_repo.values():
        ordered = sorted(repo_queries, key=lambda q: tasks[q].created_at)
        half = len(ordered) // 2
        dev += ordered[:half]
        test += ordered[half:]
    return sorted(dev), sorted(test)
