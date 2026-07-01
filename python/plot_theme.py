"""
plot_theme.py
─────────────────────────────────────────────────────────────────────────────
Single source of truth for matplotlib / seaborn styling across all notebooks
in the CRM Customer Intelligence Module.

Colour values are derived directly from light-theme.json (Power BI "BODZZ –
Warm Clay") so every chart produced in Python is palette-consistent with the
Power BI reports.

Usage
─────
    from plot_theme import apply_theme, PALETTE, SEGMENT_COLORS, ACTION_COLORS, HEALTH_COLORS, save_fig
    apply_theme()          # call once at notebook startup (Cell 3)

Public API
──────────
    apply_theme()          → sets rcParams + seaborn style
    PALETTE                → 13-key base colour dict
    SEGMENT_COLORS         → 9 RFM segment colours (matches Power BI dataColors)
    ACTION_COLORS          → 4 CRM action type colours
    HEALTH_COLORS          → 3 health tier colours
    SEQ_CMAP               → matplotlib ListedColormap for sequential heatmaps
    DIV_CMAP               → matplotlib diverging colormap (bad → neutral → good)
    save_fig(fig, name, figures_dir, dpi=150) → Path
    pct(x, total)          → float  (avoids ZeroDivisionError)
    fmt_k(x, _)            → tick formatter: 1500 → "1.5k", 1_000_000 → "1.0M"
"""

from __future__ import annotations

from pathlib import Path
from typing import Union

import matplotlib as mpl
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from matplotlib.colors import LinearSegmentedColormap, ListedColormap
import seaborn as sns

# ──────────────────────────────────────────────────────────────────────────────
# 1.  COLOUR TOKENS  (derived from light-theme.json)
# ──────────────────────────────────────────────────────────────────────────────

#: Base semantic palette — maps directly to Power BI theme semantics.
PALETTE: dict[str, str] = {
    # Structural — follow dataColors[] index order
    "primary":   "#B45F3A",   # dataColors[0]  — terracotta, first series colour
    "secondary": "#3E7E9E",   # dataColors[3]  — slate blue, supporting series
    "light":     "#6B8B5A",   # dataColors[7]  — olive green, tertiary fills

    # Semantic — mirror JSON good / neutral / bad + KPI status colours
    "accent":    "#C68A3A",   # dataColors[4]  — amber, highlight / callout
    "good":      "#5FA968",   # good / maximum — positive metric
    "bad":       "#B45F3A",   # bad  / minimum — negative / alert (= primary)
    "neutral":   "#A67B5B",   # neutral        — reference / mid-tier / goal

    # Surface / ink — pulled from textClasses & background tokens
    "warm":      "#8F6F5A",   # dataColors[6]  — dark clay, supporting context
    "ink":       "#3D3630",   # foreground / title colour (textClasses.title.color)
    "body":      "#5A524A",   # label class color — axis labels, data labels
    "muted":     "#7A7068",   # header class color — legend, subtitles, icons
    "surface":   "#FCF9F6",   # visual / card background (visualStyles.*.background)
    "bg":        "#F5F0EA",   # page background (page.background)
    "grid":      "#EDE5DC",   # gridline / divider / outspace colour
}

#: RFM segment colours — 9 segments, assigned in value-tier order so the
#: Power BI dataColors sequence and Python charts share the same hues.
SEGMENT_COLORS: dict[str, str] = {
    "Champions":            "#B45F3A",   # dataColors[0] — top tier (terracotta)
    "Loyal":                "#5FA968",   # dataColors[1] — strong positive (sage)
    "Potential Loyalist":   "#3E7E9E",   # dataColors[3] — promising (slate blue)
    "At Risk":              "#A85070",   # dataColors[2] — warning (dusty rose)
    "Can't Lose":           "#C68A3A",   # dataColors[4] — urgent (amber)
    "Needs Attention":      "#7A5F9E",   # dataColors[5] — monitor (muted violet)
    "Frequent Low-Spender": "#8F6F5A",   # dataColors[6] — contextual (dark clay)
    "Hibernating":          "#6B8B5A",   # dataColors[7] — dormant (olive)
    "Lost":                 "#2E6B85",   # dataColors[8] — gone (deep teal)
}

#: CRM action type colours — referenced in action_dev notebook and Power BI.
ACTION_COLORS: dict[str, str] = {
    "RETENTION_CAMPAIGN": PALETTE["bad"],       # terracotta — urgent
    "REACTIVATION":       PALETTE["accent"],    # amber — re-engage
    "VIP_UPGRADE":        PALETTE["secondary"], # slate blue — opportunity
    "MONITOR":            PALETTE["neutral"],   # warm clay — watch only
}

#: Customer health tier colours — used in churn / health score visuals.
HEALTH_COLORS: dict[str, str] = {
    "High":   PALETTE["good"],      # green
    "Medium": PALETTE["accent"],    # amber
    "Low":    PALETTE["bad"],       # terracotta
}

# ──────────────────────────────────────────────────────────────────────────────
# 2.  CUSTOM COLORMAPS
# ──────────────────────────────────────────────────────────────────────────────

#: Sequential colormap: surface → secondary → primary (heatmaps, choropleths).
#: Anchored to the JSON minimum/center/maximum diverging scale colours.
SEQ_CMAP: ListedColormap = LinearSegmentedColormap.from_list(
    "bodzz_seq",
    [PALETTE["surface"], PALETTE["secondary"], PALETTE["primary"]],
    N=256,
)

#: Diverging colormap: bad → neutral → good.
#: Colors match JSON minimum (#B45F3A) → center (#D4936A) → maximum (#5FA968).
#: Use for correlation matrices, over/under-index charts, sentiment heatmaps.
DIV_CMAP: LinearSegmentedColormap = LinearSegmentedColormap.from_list(
    "bodzz_div",
    ["#B45F3A", "#D4936A", "#5FA968"],   # minimum → center → maximum from JSON
    N=256,
)

# Register both so they're accessible via plt.get_cmap("bodzz_seq") etc.
mpl.colormaps.register(SEQ_CMAP, force=True)
mpl.colormaps.register(DIV_CMAP, force=True)

# ──────────────────────────────────────────────────────────────────────────────
# 3.  THEME APPLICATION
# ──────────────────────────────────────────────────────────────────────────────

#: Default prop_cycle — follows dataColors[] order from the JSON exactly,
#: so the first series rendered always matches Power BI's first data colour.
_PROP_CYCLE_COLORS = [
    "#B45F3A",   # dataColors[0] — terracotta
    "#5FA968",   # dataColors[1] — sage green
    "#A85070",   # dataColors[2] — dusty rose
    "#3E7E9E",   # dataColors[3] — slate blue
    "#C68A3A",   # dataColors[4] — amber
    "#7A5F9E",   # dataColors[5] — muted violet
    "#8F6F5A",   # dataColors[6] — dark clay
    "#6B8B5A",   # dataColors[7] — olive green
    "#2E6B85",   # dataColors[8] — deep teal
    "#9A7A3D",   # dataColors[9] — warm ochre
]


def apply_theme() -> None:
    """Apply the BODZZ Warm Clay matplotlib / seaborn theme.

    Call once per notebook, immediately after imports (Cell 3).  Safe to call
    multiple times — idempotent.
    """
    plt.rcParams.update({
        # ── Figure ──────────────────────────────────────────────────────────
        "figure.dpi":         120,
        "figure.figsize":     (11, 6),
        "figure.facecolor":   PALETTE["bg"],      # page background, not visual surface

        # ── Fonts ───────────────────────────────────────────────────────────
        # Inter leads the stack per JSON fontFace order; Segoe UI as Windows fallback.
        "font.family":           "sans-serif",
        "font.sans-serif":       ["Inter", "Segoe UI", "Helvetica Neue",
                                  "Arial", "DejaVu Sans"],
        "font.size":             11,               # label class fontSize
        "axes.labelsize":        11,               # label class fontSize (was 12)
        "axes.titlesize":        13,               # title class fontSize (was 14)
        "axes.titleweight":      "semibold",       # title class fontFace → SemiBold
        "axes.titlelocation":    "left",           # JSON title alignment = Left
        "axes.titlepad":         10,
        "axes.titlecolor":       PALETTE["ink"],   # title class color (#3D3630)
        "axes.labelcolor":       PALETTE["body"],  # label class color (#5A524A)
        "xtick.labelsize":       10,
        "ytick.labelsize":       10,
        "xtick.color":           PALETTE["body"],  # label class color
        "ytick.color":           PALETTE["body"],
        "legend.fontsize":       11,               # JSON legend fontSize = 11
        "legend.title_fontsize": 11,

        # ── Spines ──────────────────────────────────────────────────────────
        "axes.spines.top":    False,
        "axes.spines.right":  False,
        "axes.spines.left":   True,
        "axes.spines.bottom": True,
        "axes.linewidth":     0.8,
        "axes.edgecolor":     PALETTE["grid"],

        # ── Grid ────────────────────────────────────────────────────────────
        # JSON gridlines: solid stroke, colour #EDE5DC — no dashes specified.
        "axes.grid":          True,
        "axes.facecolor":     PALETTE["surface"],  # visual card background
        "grid.color":         PALETTE["grid"],     # #EDE5DC from JSON gridlines
        "grid.alpha":         1.0,                 # colour already muted; no extra fade
        "grid.linestyle":     "-",                 # solid, matching PBI default
        "grid.linewidth":     0.7,
        "axes.axisbelow":     True,                # grid renders behind data

        # ── Colours ─────────────────────────────────────────────────────────
        "axes.prop_cycle":    plt.cycler(color=_PROP_CYCLE_COLORS),
        "patch.edgecolor":    PALETTE["surface"],
        "patch.linewidth":    0.5,
        "text.color":         PALETTE["ink"],

        # ── Lines ───────────────────────────────────────────────────────────
        "lines.linewidth":    2.0,
        "lines.markersize":   6,

        # ── Legend ──────────────────────────────────────────────────────────
        # JSON: position TopRight, fontFamily Inter, fontSize 11, color #7A7068
        "legend.frameon":      True,
        "legend.framealpha":   0.92,
        "legend.edgecolor":    PALETTE["grid"],
        "legend.borderpad":    0.6,
        "legend.facecolor":    PALETTE["surface"],
        "legend.labelcolor":   PALETTE["muted"],   # #7A7068 from JSON legend colour
        "legend.loc":          "upper right",       # JSON: TopRight

        # ── Save ────────────────────────────────────────────────────────────
        "savefig.dpi":         150,
        "savefig.bbox":        "tight",
        "savefig.facecolor":   PALETTE["bg"],       # page background for exports
        "savefig.format":      "png",

        # ── Histogram ───────────────────────────────────────────────────────
        "hist.bins":           "auto",
    })

    sns.set_style("whitegrid", {
        "axes.facecolor":    PALETTE["surface"],
        "grid.color":        PALETTE["grid"],
        "grid.linestyle":    "-",                  # solid to match PBI gridlines
        "axes.edgecolor":    PALETTE["grid"],
        "figure.facecolor":  PALETTE["bg"],
    })

    sns.set_context("notebook", font_scale=1.0, rc={
        "lines.linewidth": 2.0,
        "patch.linewidth": 0.6,
    })

    print(
        f"✓ BODZZ Warm Clay theme applied  "
        f"[matplotlib {mpl.__version__}  |  seaborn {sns.__version__}]"
    )


# ──────────────────────────────────────────────────────────────────────────────
# 4.  HELPERS
# ──────────────────────────────────────────────────────────────────────────────

def save_fig(
    fig: "plt.Figure",
    filename: str,
    figures_dir: Union[str, Path],
    dpi: int = 150,
) -> Path:
    """Save *fig* to *figures_dir / filename* with consistent settings.

    Parameters
    ----------
    fig:         matplotlib Figure object.
    filename:    Output filename, e.g. ``"rfm_segment_distribution.png"``.
    figures_dir: Destination directory (``FIGURES_DIR`` from the notebook).
    dpi:         Dots per inch. Default 150 matches ``savefig.dpi`` rcParam.

    Returns
    -------
    Path to the saved file (useful for logging or downstream reference).
    """
    path = Path(figures_dir) / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=dpi, bbox_inches="tight", facecolor=PALETTE["bg"])
    return path


def pct(x: float, total: float) -> float:
    """Return ``100 * x / total``, safely returning 0.0 when *total* is zero."""
    return 0.0 if total == 0 else round(100 * x / total, 2)


def fmt_k(x: float, _: object = None) -> str:
    """Tick formatter: 1_500 → '1.5k', 1_000_000 → '1.0M'.

    Usage::

        ax.yaxis.set_major_formatter(mticker.FuncFormatter(fmt_k))
    """
    if abs(x) >= 1_000_000:
        return f"{x / 1_000_000:.1f}M"
    if abs(x) >= 1_000:
        return f"{x / 1_000:.1f}k"
    return f"{x:,.0f}"


def segment_palette(segments: list[str] | None = None) -> dict[str, str]:
    """Return a colour dict for the given *segments*, defaulting to all nine.

    Useful for seaborn ``palette=`` arguments::

        sns.barplot(..., palette=segment_palette(df["rfm_segment"].unique()))
    """
    if segments is None:
        return SEGMENT_COLORS
    return {s: SEGMENT_COLORS[s] for s in segments if s in SEGMENT_COLORS}


# Convenience re-export so notebooks need only one import line.
__all__ = [
    "apply_theme",
    "PALETTE",
    "SEGMENT_COLORS",
    "ACTION_COLORS",
    "HEALTH_COLORS",
    "SEQ_CMAP",
    "DIV_CMAP",
    "save_fig",
    "pct",
    "fmt_k",
    "segment_palette",
]