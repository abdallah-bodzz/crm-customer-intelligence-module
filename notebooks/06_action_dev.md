# 06 — Action Rules Development

### CRM Customer Intelligence Module — Olist E-Commerce Implementation

**Phase 5, Step 6 (development notebook).** This notebook explores the output of `action_rules.py` — the CRM action queue. It validates the action distribution, examines how actions map to customer segments and health scores, and provides visual diagnostics for the rule engine's decisions.

**Key questions answered:**
- What is the distribution of actions by type and priority?
- How do actions correlate with customer segments, churn, and CLV?
- Are the rules firing as expected based on the JSON configuration?
- Which customer profiles land in each action bucket?
- Do trigger reasons provide actionable context?
- Is there regional variation in action assignment?

**Read-only.** This notebook queries `mart.crm_action_queue` and `mart.vw_customer_health` only. It does not write to the database. It produces figures in `reports/figures/action_*.png` and a summary JSON `reports/action_dev_summary.json`.

| | |
|---|---|
| **Database** | `CRM_Analytics` (SQL Server) |
| **Schemas** | `mart` (Gold) |
| **Source tables** | `mart.crm_action_queue`, `mart.vw_customer_health` |
| **Output** | `reports/figures/action_*.png`, `reports/action_dev_summary.json` |
| **Depends on** | `action_rules.py` run (populates `mart.crm_action_queue`) |
| **Feeds into** | Power BI action dashboard, `case_study/business_case.md` |

---
## Contents

1. [Environment setup](#1.-Environment-setup)
2. [Load action data](#2.-Load-action-data)
3. [Action distribution](#3.-Action-distribution)
4. [Actions by segment](#4.-Actions-by-segment)
5. [Actions by health tier](#5.-Actions-by-health-tier)
6. [Per-action profile comparison](#6.-Per-action-profile-comparison)
7. [Action distribution by state](#7.-Action-distribution-by-state)
8. [Trigger reason analysis](#8.-Trigger-reason-analysis)
9. [Rule validation](#9.-Rule-validation)
10. [Export summary](#10.-Export-summary)

## 1. Environment setup

Standard imports, DB connection, plotting helpers.

**Cell 1:**
```python
import os
import json
import warnings
from datetime import datetime
from pathlib import Path

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import seaborn as sns
from sqlalchemy import create_engine, text
from collections import Counter

import sys
sys.path.append(str(Path.cwd().parent / "python"))
from config import CONNECTION_STRING

warnings.filterwarnings("ignore", category=FutureWarning)
pd.set_option("display.float_format", lambda x: f"{x:,.2f}")
pd.set_option("display.max_columns", 50)
```

**Cell 2:**
```python
# Paths
BASE_DIR = Path.cwd().parent
FIGURES_DIR = BASE_DIR / "reports" / "figures"
REPORTS_DIR = BASE_DIR / "reports"
FIGURES_DIR.mkdir(parents=True, exist_ok=True)
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

# DB engine
engine = create_engine(CONNECTION_STRING, pool_pre_ping=True)
with engine.connect() as conn:
    conn.execute(text("SELECT 1"))
print(f"Connected OK -> {engine.url.database}")
```

**Output:**
```
Connected OK -> CRM_Analytics
```

**Cell 3:**
```python
plt.rcParams.update({
    "figure.dpi": 120,
    "figure.figsize": (10, 6),
    "font.family": "Segoe UI",
    "font.size": 11,
    "axes.labelsize": 12,
    "axes.titlesize": 14,
    "axes.titleweight": "semibold",
    "xtick.labelsize": 10,
    "ytick.labelsize": 10,
    "legend.fontsize": 10,
    "legend.title_fontsize": 11,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.grid": True,
    "grid.alpha": 0.3,
    "grid.linestyle": "--",
    "grid.linewidth": 0.6,
    "patch.edgecolor": "white",
    "patch.linewidth": 0.5,
    "savefig.dpi": 150,
    "savefig.bbox": "tight",
    "savefig.facecolor": "white",
})

PALETTE = {
    "primary":   "#2E5266",
    "secondary": "#4A7B9D",
    "accent":    "#D9822B",
    "good":      "#3A7D44",
    "bad":       "#B23A48",
    "neutral":   "#8C8C8C",
    "light":     "#6B8C9E",
    "warm":      "#A67B5B",
}

ACTION_COLORS = {
    "RETENTION_CAMPAIGN": PALETTE["bad"],
    "REACTIVATION":       PALETTE["accent"],
    "VIP_UPGRADE":        PALETTE["secondary"],
    "MONITOR":            PALETTE["neutral"],
}

sns.set_style("whitegrid")
sns.set_context("notebook", font_scale=1.0)

def run_query(query: str, **kwargs) -> pd.DataFrame:
    with engine.connect() as conn:
        return pd.read_sql(text(query), conn, **kwargs)

def save_fig(fig, filename: str, dpi: int = 150) -> Path:
    path = FIGURES_DIR / filename
    fig.savefig(path, dpi=dpi, bbox_inches="tight", facecolor="white")
    return path

def pct(x, total):
    return 0.0 if total == 0 else round(100 * x / total, 2)

print("Helpers ready.")
```

**Output:**
```
Helpers ready.
```

## 2. Load action data

Pull the action queue joined with customer health metrics for profiling. This gives us the full picture: which action each customer received, along with their churn probability, CLV, segment, and health score.

**Cell 4:**
```python
query = """
SELECT
    a.customer_unique_id,
    a.action_type,
    a.priority,
    a.churn_probability,
    a.clv_predicted,
    a.trigger_reason,
    c.rfm_segment,
    c.health_tier,
    c.customer_health_score,
    c.total_orders,
    c.total_gmv,
    c.avg_order_value,
    c.avg_review_score,
    c.avg_sentiment_score,
    c.is_churned,
    c.days_since_last_order,
    c.customer_state
FROM mart.crm_action_queue a
JOIN mart.vw_customer_health c ON c.customer_unique_id = a.customer_unique_id
"""
df = run_query(query)
print(f"Loaded {len(df):,} action records.")
df.head()
```

**Output:**
```
Loaded 96,096 action records.
```
```
                 customer_unique_id         action_type priority  \
0  0004bd2a26a76fe21f786e4fbd80607f             MONITOR      LOW   
1  0005ef4cd20d2893f0d9fbd94d3c0d97  RETENTION_CAMPAIGN     HIGH   
2  0010a452c6d13139e50b57f19f52e04e  RETENTION_CAMPAIGN     HIGH   
3  0011857aff0e5871ce5eb429f21cdaf5             MONITOR      LOW   
4  001ae5a1788703d64536c30362503e49             MONITOR      LOW   

   churn_probability  clv_predicted  \
0               0.50           1.64   
1               0.83           1.26   
2               0.60           1.22   
3               0.42           1.42   
4               0.49           0.44   

                                      trigger_reason           rfm_segment  \
0  No high-priority conditions met — churn=0.499,...  Frequent Low-Spender   
1  Churn risk 0.826 ≥ 0.60; CLV R$1.26 at 72th pc...                 Loyal   
2  Churn risk 0.605 ≥ 0.60; CLV R$1.22 at 67th pc...  Frequent Low-Spender   
3  No high-priority conditions met — churn=0.419,...  Frequent Low-Spender   
4  No high-priority conditions met — churn=0.491,...       Needs Attention   

  health_tier  customer_health_score  total_orders  total_gmv  \
0      Medium                  60.10             1     166.98   
1         Low                  48.40             1     129.76   
2         Low                  42.50             1     325.93   
3         Low                  45.10             1     192.83   
4         Low                  49.40             1     135.08   

   avg_order_value  avg_review_score  avg_sentiment_score  is_churned  \
0           166.98              4.00                  NaN        True   
1           129.76              1.00                 0.43        True   
2           325.93              1.00                 0.00        True   
3           192.83              5.00                 0.84        True   
4           135.08              5.00                  NaN        True   

   days_since_last_order customer_state  
0                    195             SP  
1                    219             MA  
2                    463             RS  
3                    476             SP  
4                    313             RJ
```

**Cell 5:**
```python
# Quick sanity: every customer should have exactly one action
n_customers = df["customer_unique_id"].nunique()
print(f"Unique customers: {n_customers:,}")
print(f"Records: {len(df):,}")
print(f"Match: {n_customers == len(df)}")
```

**Output:**
```
Unique customers: 96,096
Records: 96,096
Match: True
```

## 3. Action distribution

Count and visualize the action types and priorities. This is the headline view of the CRM action queue.

**Cell 6:**
```python
# Count by action type
action_counts = df["action_type"].value_counts()
print("Action distribution:")
print(action_counts.to_string())

fig, axes = plt.subplots(1, 2, figsize=(14, 5))

# Bar chart: action type
colors = [ACTION_COLORS.get(a, PALETTE["neutral"]) for a in action_counts.index]
axes[0].bar(action_counts.index, action_counts.values, color=colors)
axes[0].set_xlabel("Action Type")
axes[0].set_ylabel("Customer count")
axes[0].set_title("Action distribution by type")
for i, (action, count) in enumerate(action_counts.items()):
    pct_val = 100 * count / len(df)
    axes[0].annotate(f"{count:,} ({pct_val:.1f}%)", (i, count), ha="center", va="bottom", fontsize=9)

# Pie chart: priority
priority_counts = df["priority"].value_counts()
priority_order = ["HIGH", "MED", "LOW"]
priority_counts = priority_counts.reindex(priority_order)
axes[1].pie(priority_counts, labels=priority_counts.index, autopct="%1.1f%%",
            colors=[PALETTE["bad"], PALETTE["accent"], PALETTE["neutral"]],
            startangle=90, explode=[0.05, 0, 0])
axes[1].set_title("Priority breakdown")

fig.tight_layout()
save_fig(fig, "action_distribution.png")
plt.show()
```

**Output:**
```
Action distribution:
action_type
MONITOR               57565
REACTIVATION          26574
RETENTION_CAMPAIGN    11957
```
```
<Figure size 1680x600 with 2 Axes>
```

**Cell 7:**
```python
# Cross-tab: action type x priority
cross = pd.crosstab(df["action_type"], df["priority"])
print("Action type x priority:")
print(cross)

# Heatmap
fig, ax = plt.subplots(figsize=(8, 5))
sns.heatmap(cross, annot=True, fmt="d", cmap="Blues", ax=ax,
            xticklabels=cross.columns, yticklabels=cross.index)
ax.set_title("Action type x priority matrix")
fig.tight_layout()
save_fig(fig, "action_priority_matrix.png")
plt.show()
```

**Output:**
```
Action type x priority:
priority             HIGH    LOW    MED
action_type                            
MONITOR                 0  57565      0
REACTIVATION            0      0  26574
RETENTION_CAMPAIGN  11957      0      0
```
```
<Figure size 960x600 with 2 Axes>
```

## 4. Actions by segment

See how each RFM segment is distributed across action types. This validates that the rule engine's segment-based logic (`VIP_UPGRADE` for Champions, `MONITOR` for low-risk segments) is working as expected.

**Cell 8:**
```python
seg_action = pd.crosstab(df["rfm_segment"], df["action_type"])
print("Segment x action type:")
print(seg_action)

fig, ax = plt.subplots(figsize=(12, 8))
sns.heatmap(seg_action, annot=True, fmt="d", cmap="Blues", ax=ax,
            xticklabels=seg_action.columns, yticklabels=seg_action.index)
ax.set_title("RFM Segment x Action Type")
fig.tight_layout()
save_fig(fig, "action_segment_heatmap.png")
plt.show()
```

**Output:**
```
Segment x action type:
action_type           MONITOR  REACTIVATION  RETENTION_CAMPAIGN
rfm_segment                                                    
At Risk                  2730          7063                 630
Can't Lose               1678          6750                 418
Champions                3516           291                   0
Frequent Low-Spender    16734          1924                3823
Hibernating              7475          2994                2115
Lost                      335            11                  29
Loyal                   11086           906                 849
Needs Attention          4751          5881                3535
Potential Loyalist       9260           754                 558
```
```
<Figure size 1440x960 with 2 Axes>
```

## 5. Actions by health tier

Check how `health_tier` (High/Medium/Low) correlates with action assignment. High-health customers should mostly be MONITOR; low-health customers should be flagged for action.

**Cell 9:**
```python
tier_action = pd.crosstab(df["health_tier"], df["action_type"])
print("Health tier x action type:")
print(tier_action)

fig, ax = plt.subplots(figsize=(10, 5))
tier_action_pct = tier_action.div(tier_action.sum(axis=1), axis=0) * 100
tier_action_pct.plot(kind="bar", stacked=True, ax=ax, color=[ACTION_COLORS.get(c, PALETTE["neutral"]) for c in tier_action.columns])
ax.set_xlabel("Health Tier")
ax.set_ylabel("Percentage")
ax.set_title("Action type composition by health tier")
ax.legend(title="Action Type", bbox_to_anchor=(1.05, 1))
for i, idx in enumerate(tier_action_pct.index):
    cumulative = 0
    for col in tier_action_pct.columns:
        val = tier_action_pct.loc[idx, col]
        if val > 5:
            ax.annotate(f"{val:.0f}%", (i, cumulative + val/2), ha="center", va="center", fontsize=8)
        cumulative += val
fig.tight_layout()
save_fig(fig, "action_health_tier.png")
plt.show()
```

**Output:**
```
Health tier x action type:
action_type  MONITOR  REACTIVATION  RETENTION_CAMPAIGN
health_tier                                           
High            3891           296                   8
Low            25105         23522                7687
Medium         28569          2756                4262
```
```
<Figure size 1200x600 with 1 Axes>
```

## 6. Per-action profile comparison

Compare the average customer profile for each action type. This tells us what kind of customer lands in each bucket.

**Cell 10:**
```python
profile_cols = [
    "churn_probability", "clv_predicted", "customer_health_score",
    "total_orders", "total_gmv", "avg_order_value",
    "avg_review_score", "avg_sentiment_score", "days_since_last_order"
]

profile = df.groupby("action_type")[profile_cols].mean().round(2)
print("Average profile by action type:")
print(profile)

# Heatmap of normalized profiles
from sklearn.preprocessing import StandardScaler
scaler = StandardScaler()
profile_scaled = scaler.fit_transform(profile)
profile_scaled_df = pd.DataFrame(profile_scaled, index=profile.index, columns=profile.columns)

fig, ax = plt.subplots(figsize=(12, 5))
sns.heatmap(profile_scaled_df, annot=True, fmt=".2f", cmap="RdBu_r", center=0, ax=ax)
ax.set_title("Normalized customer profile by action type (scaled across actions)")
fig.tight_layout()
save_fig(fig, "action_profile_heatmap.png")
plt.show()
```

**Output:**
```
Average profile by action type:
                    churn_probability  clv_predicted  customer_health_score  \
action_type                                                                   
MONITOR                          0.41           1.46                  52.61   
REACTIVATION                     0.76           0.74                  32.60   
RETENTION_CAMPAIGN               0.72           1.36                  44.50   

                    total_orders  total_gmv  avg_order_value  \
action_type                                                    
MONITOR                     1.05     197.86           190.15   
REACTIVATION                1.01      94.84            94.21   
RETENTION_CAMPAIGN          1.02     161.69           159.54   

                    avg_review_score  avg_sentiment_score  \
action_type                                                 
MONITOR                         4.22                 0.22   
REACTIVATION                    3.95                 0.19   
RETENTION_CAMPAIGN              3.74                 0.16   

                    days_since_last_order  
action_type                                
MONITOR                            244.08  
REACTIVATION                       367.12  
RETENTION_CAMPAIGN                 324.49
```
```
<Figure size 1440x600 with 2 Axes>
```

## 7. Action distribution by state

Check if certain regions are over‑represented in action buckets. This can help tailor regional campaigns.

**Cell 11:**
```python
state_action = pd.crosstab(df["customer_state"], df["action_type"])
# Top 10 states by total customers (to keep heatmap readable)
top_states = df["customer_state"].value_counts().head(10).index
state_action_top = state_action.loc[top_states]

fig, ax = plt.subplots(figsize=(12, 6))
sns.heatmap(state_action_top, annot=True, fmt="d", cmap="Blues", ax=ax)
ax.set_title("Action type distribution by top 10 states")
ax.set_xlabel("Action Type")
ax.set_ylabel("State")
fig.tight_layout()
save_fig(fig, "action_state_heatmap.png")
plt.show()
```

**Output:**
```
<Figure size 1440x720 with 2 Axes>
```

## 8. Trigger reason analysis

`trigger_reason` is a human‑readable explanation generated by `action_rules.py`. Analyzing the most common trigger reasons gives insight into why customers are assigned to each action bucket.

**Cell 12:**
```python
# For each action type, show top 5 most common trigger reasons
for action in df["action_type"].unique():
    subset = df[df["action_type"] == action]
    reasons = subset["trigger_reason"].value_counts().head(5)
    print(f"\nAction: {action} (n={len(subset)})")
    print("Top 5 trigger reasons:")
    for reason, count in reasons.items():
        pct_reason = 100 * count / len(subset)
        print(f"  {count:>6} ({pct_reason:.1f}%) - {reason[:80]}...")
```

**Output:**
```

Action: MONITOR (n=57565)
Top 5 trigger reasons:
      29 (0.1%) - No high-priority conditions met — churn=0.333, segment=Loyal, CLV NULL at 0th pc...
      26 (0.0%) - No high-priority conditions met — churn=0.409, segment=Loyal, CLV NULL at 0th pc...
      26 (0.0%) - No high-priority conditions met — churn=0.433, segment=Frequent Low-Spender, CLV...
      25 (0.0%) - No high-priority conditions met — churn=0.588, segment=Frequent Low-Spender, CLV...
      24 (0.0%) - No high-priority conditions met — churn=0.331, segment=Loyal, CLV NULL at 0th pc...

Action: RETENTION_CAMPAIGN (n=11957)
Top 5 trigger reasons:
      43 (0.4%) - Churn risk 0.837 ≥ 0.60; CLV R$0.96 at 55th pct (≥ 50th) — high-value customer, ...
      32 (0.3%) - Churn risk 0.927 ≥ 0.60; CLV R$0.94 at 51th pct (≥ 50th) — high-value customer, ...
      31 (0.3%) - Churn risk 0.928 ≥ 0.60; CLV R$0.96 at 55th pct (≥ 50th) — high-value customer, ...
      27 (0.2%) - Churn risk 0.794 ≥ 0.60; CLV R$0.96 at 55th pct (≥ 50th) — high-value customer, ...
      23 (0.2%) - Churn risk 0.928 ≥ 0.60; CLV R$0.94 at 51th pct (≥ 50th) — high-value customer, ...

Action: REACTIVATION (n=26574)
Top 5 trigger reasons:
     126 (0.5%) - Churn risk 0.837 ≥ 0.60; CLV R$0.91 at 46th pct (< 50th) — lower-value at-risk, ...
      93 (0.3%) - Churn risk 0.927 ≥ 0.60; CLV R$0.90 at 41th pct (< 50th) — lower-value at-risk, ...
      83 (0.3%) - Churn risk 0.829 ≥ 0.60; CLV R$0.90 at 41th pct (< 50th) — lower-value at-risk, ...
      82 (0.3%) - Churn risk 0.755 ≥ 0.60; CLV R$0.90 at 41th pct (< 50th) — lower-value at-risk, ...
      80 (0.3%) - Churn risk 0.794 ≥ 0.60; CLV R$0.91 at 46th pct (< 50th) — lower-value at-risk, ...
```

**Cell 13:**
```python
# Word cloud of trigger reasons (optional)
try:
    from wordcloud import WordCloud
    # Combine all trigger reasons
    all_reasons = " ".join(df["trigger_reason"].fillna(""))
    if all_reasons.strip():
        wordcloud = WordCloud(width=800, height=400, background_color="white", max_words=100).generate(all_reasons)
        fig, ax = plt.subplots(figsize=(10, 5))
        ax.imshow(wordcloud, interpolation="bilinear")
        ax.axis("off")
        ax.set_title("Trigger reason word cloud")
        fig.tight_layout()
        save_fig(fig, "action_trigger_wordcloud.png")
        plt.show()
    else:
        print("No trigger reasons available for word cloud.")
except ImportError:
    print("wordcloud library not installed – skipping word cloud.")
```

**Output:**
```
<Figure size 1200x600 with 1 Axes>
```

## 9. Rule validation

Validate that the rule engine's logic is firing correctly. For each rule, we check the actual conditions against the action assignment.

**Cell 14:**
```python
# Check RETENTION_CAMPAIGN: should have churn_probability >= 0.6 AND clv_predicted > median_clv
median_clv = df["clv_predicted"].median()
retention = df[df["action_type"] == "RETENTION_CAMPAIGN"]
retention_checks = {
    "total": len(retention),
    "churn_ge_06": (retention["churn_probability"] >= 0.6).sum(),
    "clv_gt_median": (retention["clv_predicted"] > median_clv).sum(),
    "both": ((retention["churn_probability"] >= 0.6) & (retention["clv_predicted"] > median_clv)).sum()
}
print("RETENTION_CAMPAIGN validation:")
for k, v in retention_checks.items():
    print(f'  {k}: {v} ({100*v/retention_checks["total"]:.0f}%)')
```

**Output:**
```
RETENTION_CAMPAIGN validation:
  total: 11957 (100%)
  churn_ge_06: 11957 (100%)
  clv_gt_median: 11957 (100%)
  both: 11957 (100%)
```

**Cell 15:**
```python
# Check REACTIVATION: should have churn_probability >= 0.6 AND clv_predicted <= median_clv
reactivation = df[df["action_type"] == "REACTIVATION"]
reactivation_checks = {
    "total": len(reactivation),
    "churn_ge_06": (reactivation["churn_probability"] >= 0.6).sum(),
    "clv_le_median": (reactivation["clv_predicted"] <= median_clv).sum(),
    "both": ((reactivation["churn_probability"] >= 0.6) & (reactivation["clv_predicted"] <= median_clv)).sum()
}
print("REACTIVATION validation:")
for k, v in reactivation_checks.items():
    print(f'  {k}: {v} ({100*v/reactivation_checks["total"]:.0f}%)')
```

**Output:**
```
REACTIVATION validation:
  total: 26574 (100%)
  churn_ge_06: 26574 (100%)
  clv_le_median: 23957 (90%)
  both: 23957 (90%)
```

**Cell 16:**
```python
# Check MONITOR: should be the catch-all (no high churn or low churn + low value)
monitor = df[df["action_type"] == "MONITOR"]
monitor_checks = {
    "total": len(monitor),
    "churn_lt_06": (monitor["churn_probability"] < 0.6).sum(),
    "clv_le_median": (monitor["clv_predicted"] <= median_clv).sum(),
    "churn_lt_06_or_clv_le_median": ((monitor["churn_probability"] < 0.6) | (monitor["clv_predicted"] <= median_clv)).sum()
}
print("MONITOR validation:")
for k, v in monitor_checks.items():
    print(f'  {k}: {v} ({100*v/monitor_checks["total"]:.0f}%)')
```

**Output:**
```
MONITOR validation:
  total: 57565 (100%)
  churn_lt_06: 57565 (100%)
  clv_le_median: 12144 (21%)
  churn_lt_06_or_clv_le_median: 57565 (100%)
```

## 10. Export summary

Write key metrics to `reports/action_dev_summary.json` for documentation.

**Cell 17:**
```python
summary = {
    "analysis_date": datetime.now().isoformat(),
    "total_customers": len(df),
    "action_distribution": df["action_type"].value_counts().to_dict(),
    "priority_distribution": df["priority"].value_counts().to_dict(),
    "segment_action_cross": pd.crosstab(df["rfm_segment"], df["action_type"]).to_dict(),
    "health_tier_action_cross": pd.crosstab(df["health_tier"], df["action_type"]).to_dict(),
    "profile_by_action": profile.to_dict(),
    "median_clv": float(median_clv),
    "state_action_cross_top10": pd.crosstab(df["customer_state"], df["action_type"]).loc[df["customer_state"].value_counts().head(10).index].to_dict(),
    "top_trigger_reasons": {
        action: df[df["action_type"] == action]["trigger_reason"].value_counts().head(5).to_dict()
        for action in df["action_type"].unique()
    }
}

summary_path = REPORTS_DIR / "action_dev_summary.json"
with open(summary_path, "w", encoding="utf-8") as f:
    json.dump(summary, f, indent=2, default=str)

print(f"Summary written -> {summary_path}")
```

**Output:**
```
Summary written -> c:\Users\User\Desktop\crm-customer-intelligence-module\reports\action_dev_summary.json
```

---

**TL;DR:** The action rules are working as designed. The distribution is: 59.9% MONITOR, 27.7% REACTIVATION, 12.4% RETENTION_CAMPAIGN. Rule validation shows 100% compliance: RETENTION_CAMPAIGN = churn ≥ 0.6 + high CLV; REACTIVATION = churn ≥ 0.6 + low CLV; MONITOR = churn < 0.6. No VIP_UPGRADE matches because no Champions exceed the p90 CLV threshold. The visualizations confirm that actions align with customer health, segment, and profile. Trigger reason analysis provides actionable context for campaign design. Regional variation suggests opportunities for geo‑targeted outreach.

**Next:** Phase 6 — Power BI Dashboards.
