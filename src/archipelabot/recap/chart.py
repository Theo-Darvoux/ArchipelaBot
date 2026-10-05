import io
from collections.abc import Callable
from datetime import datetime, tzinfo

import matplotlib.dates as mdates
from matplotlib.figure import Figure
from matplotlib.ticker import PercentFormatter

from .stats import Recap, Series

BACKGROUND = "#2B2D31"
GRID = "#3F4147"
TEXT = "#DBDEE1"
MUTED = "#949BA4"
GOLD = "#F0B232"
# Distinct colours that stay readable on Discord's dark background, starting with Archipelago's.
PALETTE = [
    "#AF99EF", "#6D8BE8", "#00D2D2", "#FA8072", "#F0B232", "#57F287", "#EB459E", "#FEE75C",
    "#5865F2", "#ED4245", "#3BA55C", "#FAA61A", "#9B84EE", "#45DDC0", "#F47FFF", "#B9BBBE",
]  # fmt: skip
FONT = {"family": ["Inter", "Noto Sans", "DejaVu Sans"], "size": 13}


def value_at(series: Series, when: datetime) -> float:
    current = series.points[0][1]
    for at, ratio in series.points:
        if at > when:
            break
        current = ratio
    return current


def render_chart(recap: Recap, name_of: Callable[[int], str], tz: tzinfo) -> bytes:
    """Completion over time for each player, with a star where they reached their goal. PNG bytes.

    Built without pyplot, whose global state isn't safe to share between the threads rendering recaps.
    """
    figure = Figure(figsize=(12, 6.5), dpi=130)
    axes = figure.subplots()
    figure.patch.set_facecolor(BACKGROUND)
    axes.set_facecolor(BACKGROUND)

    ordered = sorted(recap.series, key=lambda s: -s.points[-1][1])
    for i, series in enumerate(ordered):
        colour = PALETTE[i % len(PALETTE)]
        times = [at for at, _ in series.points]
        ratios = [ratio for _, ratio in series.points]
        axes.plot(times, ratios, drawstyle="steps-post", color=colour, linewidth=2.2, label=name_of(series.slot))
        if series.goal_at:
            axes.plot([series.goal_at], [value_at(series, series.goal_at)], marker="*", markersize=18,
                      color=GOLD, markeredgecolor=BACKGROUND, markeredgewidth=1.2, zorder=5)  # fmt: skip

    axes.set_ylim(0, 1.04)
    axes.yaxis.set_major_formatter(PercentFormatter(1.0, decimals=0))
    span = recap.ended - recap.started
    axes.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m %Hh" if span.days else "%Hh%M", tz=tz))
    axes.xaxis.set_major_locator(mdates.AutoDateLocator(tz=tz, minticks=3, maxticks=9))
    axes.grid(color=GRID, linewidth=0.8)
    axes.tick_params(colors=MUTED, length=0, labelsize=FONT["size"], labelfontfamily=FONT["family"])
    for spine in axes.spines.values():
        spine.set_visible(False)

    legend = axes.legend(
        loc="upper center", bbox_to_anchor=(0.5, -0.1), ncol=min(4, max(1, len(ordered))),
        frameon=False, labelcolor=TEXT, handlelength=1.4, prop=FONT,
    )  # fmt: skip
    for line in legend.get_lines():
        line.set_linewidth(4)

    figure.tight_layout()
    buffer = io.BytesIO()
    figure.savefig(buffer, format="png", facecolor=BACKGROUND)
    return buffer.getvalue()
