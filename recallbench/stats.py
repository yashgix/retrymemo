"""Percentile bootstrap over queries and over repositories, shared by both experiments."""

import numpy as np

SEED = 20261114
BOOTSTRAP_DRAWS = 1000


def r6(value) -> float:
    return round(float(value), 6)


def draws(n: int) -> np.ndarray:
    """Index draws with replacement, the same for every compared array (a paired bootstrap)."""
    return np.random.default_rng(SEED).integers(0, n, (BOOTSTRAP_DRAWS, n))


def interval(means) -> tuple[float, float]:
    means = np.sort(np.asarray(means, dtype=float))
    return float(means[BOOTSTRAP_DRAWS // 40]), float(means[-1 - BOOTSTRAP_DRAWS // 40])


def query_interval(values, query_draws: np.ndarray) -> tuple[float, float]:
    return interval(np.asarray(values, dtype=float)[query_draws].mean(axis=1))


def repo_interval(values, repo_of, repo_draws: np.ndarray) -> tuple[float, float]:
    """Resample whole repositories. `repo_of[j]` is the repo index (0..R-1, each with a query) of query j."""
    sums = np.bincount(repo_of, weights=np.asarray(values, dtype=float), minlength=repo_draws.shape[1])
    counts = np.bincount(repo_of, minlength=repo_draws.shape[1])
    return interval(sums[repo_draws].sum(axis=1) / counts[repo_draws].sum(axis=1))


def intervals(values, repo_of, n_repos: int) -> dict[str, list[float]]:
    """95% intervals over queries and over repositories, rounded for the results files."""
    return {
        "query_ci": [r6(v) for v in query_interval(values, draws(len(values)))],
        "repo_ci": [r6(v) for v in repo_interval(values, repo_of, draws(n_repos))],
    }
