"""Render the generated results block and print a one-line summary."""

import argparse
import json
from pathlib import Path

from recallbench.retrieve import RETRIEVERS
from recallbench.run import FILES_NAME

CARD_NOTE = (
    "Both forms are scored on the same file pointers, so the card's lead in this table is a budget effect, true "
    "by construction: more cards fit. It does not show that a card helps an agent as much as a full session."
)
START = "<!-- results:start -->"
END = "<!-- results:end -->"


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _points(value: float) -> str:
    return f"{value * 100:+.1f}"


def _tokens(value: float) -> str:
    return f"{value:.0f}"


def _table(header: list[str], rows: list[list[str]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return lines


def _retriever_name(index: int, best: int) -> str:
    name = RETRIEVERS[index]
    return f"**`{name}`** (best)" if index == best else f"`{name}`"


def render_session_block(metrics: dict, repos: list[str]) -> str:
    """`repos` are the evaluated corpus's repositories, sorted, in the order of the per-repository lists."""
    best = metrics["best_retriever"]
    recent = RETRIEVERS.index("recent")
    issue = RETRIEVERS.index("bm25_issue")
    n_budgets = len(metrics["budgets"])
    n_repos = len(repos)

    lines = [
        (
            f"Across {metrics['queries']} queries from {metrics['repos']} repositories "
            f"({metrics['tasks']} tasks, {metrics['sessions']} sessions, "
            f"{metrics['resolved_sessions']} resolved sessions), the mean pool has "
            f"{metrics['mean_pool']:.1f} sessions and {metrics['answerable']} queries have "
            f"a memory that points to a file the real fix touched. The mean full-session "
            f"cost is {_tokens(metrics['mean_full_tokens'])} tokens and the mean card cost "
            f"is {_tokens(metrics['mean_card_tokens'])} tokens."
        ),
        "",
    ]

    retriever_rows = []
    for i in range(len(RETRIEVERS)):
        retriever_rows.append(
            [
                _retriever_name(i, best),
                _pct(metrics["hit1"][i]),
                f"{_pct(metrics['hit5'][i])} ({_pct(metrics['hit5_lo'][i])}-{_pct(metrics['hit5_hi'][i])})",
                _pct(metrics["recall5"][i]),
                f"{metrics['ndcg5'][i]:.3f}",
                f"{_points(metrics['hit5_vs_issue'][i])} "
                f"({_points(metrics['hit5_vs_issue_lo'][i])} to "
                f"{_points(metrics['hit5_vs_issue_hi'][i])})",
                _tokens(metrics["full_tokens5"][i]),
                _tokens(metrics["card_tokens5"][i]),
            ]
        )
    lines.extend(
        _table(
            [
                "Retriever",
                "hit@1",
                "hit@5 (95% CI)",
                "recall@5",
                "nDCG@5",
                "hit@5 vs bm25_issue",
                "tokens@5 full",
                "tokens@5 card",
            ],
            retriever_rows,
        )
    )
    lines.append("")

    budget_rows = []
    for b, budget in enumerate(metrics["budgets"]):
        budget_rows.append(
            [
                str(budget),
                _pct(metrics["full_budget_hit"][recent * n_budgets + b]),
                _pct(metrics["card_budget_hit"][recent * n_budgets + b]),
                _pct(metrics["full_budget_hit"][best * n_budgets + b]),
                _pct(metrics["card_budget_hit"][best * n_budgets + b]),
            ]
        )
    lines.extend(
        _table(
            [
                "Budget",
                "recent full",
                "recent card",
                f"{RETRIEVERS[best]} full",
                f"{RETRIEVERS[best]} card",
            ],
            budget_rows,
        )
    )
    lines.extend(["", CARD_NOTE, ""])

    repo_rows = []
    for r, repo in enumerate(repos):
        repo_rows.append(
            [
                repo,
                str(metrics["repo_queries"][r]),
                str(metrics["repo_answerable"][r]),
                _pct(metrics["repo_hit5"][recent * n_repos + r]),
                _pct(metrics["repo_hit5"][issue * n_repos + r]),
                _pct(metrics["repo_hit5"][best * n_repos + r]),
            ]
        )
    lines.extend(
        _table(
            ["Repository", "Queries", "Answerable", "recent", "bm25_issue", RETRIEVERS[best]],
            repo_rows,
        )
    )
    return "\n".join(lines)


SHOWN = ("vote_prior", "issue_fix_vote", "past_fix_files", "hybrid", "bm25_issue", "most_files")


def _verdict(comparison: dict) -> str:
    lows = (comparison["query_ci"][0], comparison["repo_ci"][0])
    highs = (comparison["query_ci"][1], comparison["repo_ci"][1])
    if min(lows) > 0:
        return f"`{comparison['a']}` is better"
    if max(highs) < 0:
        return f"`{comparison['a']}` is worse"
    if lows[0] > 0 or highs[0] < 0:
        return "clear over queries, not over repositories"
    return "no clear difference"


def _interval(ci: list[float]) -> str:
    return f"{_points(ci[0])} to {_points(ci[1])}"


def _main(files: dict) -> int:
    return files["ns"].index(files["main_n"])


def _file_headline(files: dict) -> str:
    n, test = files["main_n"], files["test"]
    rates = ", ".join(f"`{name}` {_pct(test['file_hit'][name][_main(files)])}" for name in SHOWN)
    return (
        f"Main metric, file hit@{n}: take the first {n} distinct files that a ranking names, walking its sessions in "
        f"rank order with edited files first, and count a hit when one of them is a file the real fix changed. "
        f"Every ranking is held to the same number of files, so naming more files cannot raise its score. "
        f"The test half is the later half of each repository's queries ({test['queries']} queries). The `vote_prior` "
        f"settings and the v1-style best retriever were chosen on the development half before the test half was "
        f"scored. Test half: {rates}."
    )


def _comparison_rows(half: dict) -> list[list[str]]:
    return [
        [
            c["metric"],
            f"`{c['a']}` vs `{c['b']}`",
            _points(c["diff"]),
            _interval(c["query_ci"]),
            _interval(c["repo_ci"]),
            _verdict(c),
        ]
        for c in half["comparisons"]
    ]


def _ranker_rows(files: dict, half: dict) -> list[list[str]]:
    main = _main(files)
    rows = []
    for name in files["rankers"]:
        ci = half["file_hit_main_ci"][name]
        session = name in half["session_hit5"]
        rows.append(
            [
                name,
                *(_pct(value) for value in half["file_hit"][name]),
                f"{_pct(ci['query_ci'][0])}-{_pct(ci['query_ci'][1])}",
                f"{_pct(ci['repo_ci'][0])}-{_pct(ci['repo_ci'][1])}",
                _pct(half["file_recall"][name][main]),
                _pct(half["session_hit5"][name]) if session else "n/a",
                f"{half['files_named5'][name]:.1f}" if session else "n/a",
            ]
        )
    return rows


def _repo_rows(half: dict) -> list[list[str]]:
    paired = half["repo_main_comparison"]
    rows = [
        [
            repo,
            *(_pct(half["repo_file_hit_main"][name][r]) for name in SHOWN),
            f"{_points(paired['diff'][r])} ({_interval(paired['query_ci'][r])})",
            str(half["repo_queries"][r]),
        ]
        for r, repo in enumerate(half["repos"])
    ]
    rows.append(["Mean of repositories", *(_pct(half["macro_file_hit_main"][name]) for name in SHOWN), "", ""])
    return rows


def _cost_cells(costs: dict, form: str) -> list[str]:
    if f"{form}_tokens" not in costs:
        return ["n/a", "n/a"]
    return [_tokens(costs[f"{form}_tokens"]), f"{costs[f'{form}_per_1k']:.2f}"]


def _token_rows(files: dict, half: dict) -> list[list[str]]:
    rows = []
    for name in files["rankers"]:
        costs = half["tokens"][name]
        cells = [cell for form in ("list", "card", "full") for cell in _cost_cells(costs, form)]
        rows.append([name, f"{costs['found5']:.2f}", *cells])
    return rows


def _half_rows(files: dict) -> list[list[str]]:
    main = _main(files)
    return [
        [name, *(_pct(files[h]["file_hit"][name][main]) for h in ("dev", "test", "all"))] for name in files["rankers"]
    ]


def _dev_choice(files: dict) -> str:
    name = files["dev_choice"]
    dev, test = files["dev"]["session_hit5"][name], files["test"]["session_hit5"][name]
    return (
        f"The v1 rule (highest session hit@5) applied to the development half picks `{name}`, with session hit@5 "
        f"{_pct(dev)} there and {_pct(test)} on the test half."
    )


def render_file_block(files: dict) -> str:
    n, test = files["main_n"], files["test"]
    lines = ["### File level, test half (main result)", "", _file_headline(files), ""]
    lines += _table(
        ["Metric", "Comparison", "Difference (points)", "95% CI, queries", "95% CI, repositories", "Reading"],
        _comparison_rows(test),
    )
    lines += [
        "",
        "A difference is called clear only when both intervals exclude zero. The repository interval resamples whole "
        "repositories, so it shows how much the result depends on which repositories are in the data.",
        "",
    ]
    lines += _table(
        [
            "Ranking",
            *(f"file hit@{k}" for k in files["ns"]),
            f"hit@{n} CI, queries",
            f"hit@{n} CI, repositories",
            f"recall@{n}",
            "session hit@5 (v1)",
            "files named by top 5 sessions",
        ],
        _ranker_rows(files, test),
    )
    lines += ["", f"File hit@{n} by repository, test half:", ""]
    paired = test["repo_main_comparison"]
    lines += _table(
        ["Repository", *SHOWN, f"{paired['a']} - {paired['b']} (95% CI, queries)", "Queries"], _repo_rows(test)
    )
    lines += [
        "",
        f"Memory cost, test half: gold files found among the first {n} files, and the tokens needed to inject them "
        "as a plain file list, as the cards of the sessions that supplied them, or as those full sessions. "
        "Per 1k is gold files found per 1,000 injected tokens.",
        "",
    ]
    lines += _table(
        ["Ranking", "Gold files found", "File list", "per 1k", "Cards", "per 1k", "Full sessions", "per 1k"],
        _token_rows(files, test),
    )
    lines += ["", "### Development half and all queries", "", _dev_choice(files), ""]
    lines += _table(["Ranking", f"file hit@{n}, dev", f"file hit@{n}, test", f"file hit@{n}, all"], _half_rows(files))
    lines += ["", "The development half is where `vote_prior` was tuned, so its development number is optimistic.", ""]
    lines.append("Test-half misses of `vote_prior` are classified in `docs/MISSES.md`.")
    return "\n".join(lines)


def render_block(metrics: dict, files: dict) -> str:
    session = render_session_block(metrics, files["repos"])
    return "\n".join(
        [render_file_block(files), "", "### Session level, all queries (v1 metrics, unchanged)", "", session]
    )


def _line_index(lines: list[str], marker: str, start: int = 0) -> int:
    for i in range(start, len(lines)):
        if lines[i].strip() == marker:
            return i
    raise ValueError(f"missing marker: {marker}")


def replace_results(text: str, block: str) -> str:
    lines = text.splitlines(keepends=True)
    start = _line_index(lines, START)
    end = _line_index(lines, END, start + 1)
    before = "".join(lines[: start + 1])
    after = "".join(lines[end:])
    return before + block.rstrip("\n") + "\n" + after


def summary(metrics: dict, files: dict) -> str:
    best = metrics["best_retriever"]
    recent = RETRIEVERS.index("recent")
    issue = RETRIEVERS.index("bm25_issue")
    return (
        f"hit@5: {RETRIEVERS[recent]} {_pct(metrics['hit5'][recent])}, "
        f"{RETRIEVERS[issue]} {_pct(metrics['hit5'][issue])}, "
        f"{RETRIEVERS[best]} {_pct(metrics['hit5'][best])} (best); "
        f"tokens per hit for {RETRIEVERS[best]}: "
        f"cards {_tokens(metrics['card_tokens_per_hit'][best])}, "
        f"full {_tokens(metrics['full_tokens_per_hit'][best])}; "
        f"test-half file hit@{files['main_n']}: "
        + ", ".join(f"{name} {_pct(files['test']['file_hit'][name][_main(files)])}" for name in SHOWN)
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=Path("results"))
    parser.add_argument("--readme", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)

    if args.readme is not None and args.out is None:
        parser.error("--readme requires --out")

    metrics = json.loads((args.results / "metrics.json").read_text(encoding="utf-8"))
    files = json.loads((args.results / FILES_NAME).read_text(encoding="utf-8"))
    if args.readme is None:
        print(summary(metrics, files))
        return

    text = args.readme.read_text(encoding="utf-8")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(replace_results(text, render_block(metrics, files)), encoding="utf-8")


if __name__ == "__main__":
    main()
