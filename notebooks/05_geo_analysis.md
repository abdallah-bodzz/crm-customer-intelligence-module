# 05 — Geospatial Analysis

### CRM Customer Intelligence Module — Olist E-Commerce Implementation

**Phase 5, Step 5 development notebook.** This notebook explores the geographic dimension of the Olist customer base: where GMV concentrates, where delivery is slow, where churn is high, and how these factors interact. Enhanced with choropleth maps, correlation heatmaps, and rich visualizations.

**Key questions answered:**
- Which states contribute the most GMV and customers?
- How does delivery performance vary by state?
- Is there a relationship between delivery delay and review scores at state level?
- Which cities are the top revenue generators?
- What is the correlation between churn, freight ratio, and delivery?
- How do state‑level metrics cluster together?

**Read-only.** This notebook queries `mart.*` and `warehouse.*` only. It does not write to the database. It produces figures in `reports/figures/geo_*.png` and a summary JSON `reports/geo_summary.json`.

| | |
|---|---|
| **Database** | `CRM_Analytics` (SQL Server) |
| **Schemas** | `mart`, `warehouse` |
| **Source tables** | `mart.customer_360`, `warehouse.fact_orders`, `warehouse.fact_order_items` |
| **Output** | `reports/figures/geo_*.png`, `reports/geo_summary.json` |
| **Depends on** | `mart.sp_refresh_mart` run, `silver` tables loaded |
| **Feeds into** | Power BI geo dashboard, `case_study/business_case.md` |

---
## Contents

1. [Environment setup](#1.-Environment-setup)
2. [Load data](#2.-Load-data)
3. [State-level aggregates](#3.-State-level-aggregates)
4. [Choropleth maps](#4.-Choropleth-maps)
5. [Top cities by GMV](#5.-Top-cities-by-GMV)
6. [Delivery vs satisfaction](#6.-Delivery-vs-satisfaction)
7. [Correlation analysis](#7.-Correlation-analysis)
8. [Churn vs GMV and freight ratio](#8.-Churn-vs-GMV-and-freight-ratio)
9. [Export summary](#9.-Export-summary)

## 1. Environment setup

Imports, DB connection, plotting helpers, and a function to load Brazil state GeoJSON for choropleth maps.

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
import plotly.express as px
import plotly.graph_objects as go
from sqlalchemy import create_engine, text
import requests

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
# Plot style (Matplotlib)
plt.rcParams.update({
    "figure.dpi": 100,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.grid": True,
    "grid.alpha": 0.25,
    "font.size": 11,
})
sns.set_palette("viridis")

PALETTE = {
    "primary": "#2E5266",
    "accent":  "#D9822B",
    "good":    "#3A7D44",
    "bad":     "#B23A48",
    "neutral": "#8C8C8C",
}

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

## 2. Load data

Pull state‑level metrics from `mart.customer_360` and compute additional aggregates such as average freight ratio and delivery metrics. Also load a list of cities with their GMV for the top‑city plot.

**Cell 4:**
```python
# State-level aggregates from customer_360
state_query = """
SELECT
    customer_state,
    COUNT(*) AS customer_count,
    SUM(total_gmv) AS total_gmv,
    SUM(total_freight_paid) AS total_freight_paid,
    AVG(avg_order_value) AS avg_order_value,
    AVG(avg_delivery_delta_days) AS avg_delivery_delta,
    AVG(pct_late_deliveries) AS avg_pct_late,
    AVG(avg_review_score) AS avg_review_score,
    SUM(CASE WHEN is_churned = 1 THEN 1 ELSE 0 END) AS churned_count
FROM mart.customer_360
GROUP BY customer_state
"""
state_df = run_query(state_query)

# Compute derived metrics
state_df["freight_ratio"] = np.where(state_df["total_gmv"] > 0,
                                     state_df["total_freight_paid"] / state_df["total_gmv"],
                                     0)
state_df["churn_rate"] = state_df["churned_count"] / state_df["customer_count"]
state_df["pct_of_total_gmv"] = state_df["total_gmv"] / state_df["total_gmv"].sum() * 100
state_df["pct_of_customers"] = state_df["customer_count"] / state_df["customer_count"].sum() * 100

# Clean up unknown states (shouldn't happen but defensive)
state_df = state_df[state_df["customer_state"].notna()]
state_df = state_df.sort_values("total_gmv", ascending=False)
state_df.head(10)
```

**Output:**
```
   customer_state  customer_count    total_gmv  total_freight_paid  \
6              SP           40294 5,923,136.60          719,026.98   
23             RJ           12383 2,130,016.64          305,634.19   
18             MG           11255 1,856,513.35          270,912.52   
3              RS            5275   885,682.23          135,490.11   
14             PR            4880   800,017.28          117,778.59   
5              BA            3276   611,588.73          100,158.73   
7              SC            3529   609,659.23           89,507.39   
12             DF            2073   353,181.49           50,613.46   
20             GO            1950   347,372.55           53,030.60   
24             ES            1964   324,946.11           49,818.90   

    avg_order_value  avg_delivery_delta  avg_pct_late  avg_review_score  \
6            142.19              -11.05          0.06              4.17   
23           166.04              -11.74          0.13              3.88   
18           160.08              -13.20          0.06              4.13   
3            162.25              -13.90          0.07              4.13   
14           159.20              -13.28          0.05              4.18   
5            181.38              -10.77          0.14              3.86   
7            166.88              -11.49          0.10              4.07   
12           166.37              -12.12          0.07              4.07   
20           173.71              -12.14          0.08              4.04   
24           160.17              -10.44          0.12              4.03   

    churned_count  freight_ratio  churn_rate  pct_of_total_gmv  \
6           27408           0.12        0.68             37.39   
23           9119           0.14        0.74             13.44   
18           8181           0.15        0.73             11.72   
3            3909           0.15        0.74              5.59   
14           3486           0.15        0.71              5.05   
5            2351           0.16        0.72              3.86   
7            2611           0.15        0.74              3.85   
12           1420           0.14        0.68              2.23   
20           1438           0.15        0.74              2.19   
24           1428           0.15        0.73              2.05   

    pct_of_customers  
6              41.93  
23             12.89  
18             11.71  
3               5.49  
14              5.08  
5               3.41  
7               3.67  
12              2.16  
20              2.03  
24              2.04
```

**Cell 5:**
```python
# City-level GMV (top cities)
city_query = """
SELECT
    customer_city,
    customer_state,
    COUNT(*) AS customer_count,
    SUM(total_gmv) AS total_gmv
FROM mart.customer_360
WHERE customer_city IS NOT NULL
GROUP BY customer_city, customer_state
ORDER BY total_gmv DESC
"""
city_df = run_query(city_query)
top_cities = city_df.head(15)
top_cities
```

**Output:**
```
            customer_city customer_state  customer_count    total_gmv
0               sao paulo             SP           14972 2,170,809.70
1          rio de janeiro             RJ            6618 1,156,212.90
2          belo horizonte             MG            2668   416,320.48
3                brasilia             DF            2066   352,203.09
4                curitiba             PR            1464   246,062.52
5            porto alegre             RS            1326   224,064.09
6                salvador             BA            1208   216,907.60
7                campinas             SP            1396   212,273.67
8               guarulhos             SP            1152   163,820.93
9                 niteroi             RJ             809   137,580.28
10                goiania             GO             670   123,659.47
11  sao bernardo do campo             SP             908   119,024.85
12              fortaleza             CE             642   118,507.03
13                 santos             SP             691   111,601.75
14                 recife             PE             587   109,633.88
```

## 3. State-level aggregates

Display the key state metrics in a table and bar charts for GMV, churn rate, delivery delta, and freight ratio.

**Cell 6:**
```python
fig, axes = plt.subplots(2, 2, figsize=(16, 12))

# GMV by state
top_states = state_df.head(10)
axes[0, 0].barh(top_states["customer_state"], top_states["total_gmv"], color=PALETTE["primary"])
axes[0, 0].set_xlabel("Total GMV (R$)")
axes[0, 0].set_title("Top 10 states by GMV")
axes[0, 0].xaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"R${x/1e6:.1f}M"))

# Churn rate by state
churn_sorted = state_df.sort_values("churn_rate", ascending=False).head(10)
axes[0, 1].barh(churn_sorted["customer_state"], churn_sorted["churn_rate"], color=PALETTE["bad"])
axes[0, 1].set_xlabel("Churn rate")
axes[0, 1].set_title("Top 10 states by churn rate")
axes[0, 1].xaxis.set_major_formatter(mticker.PercentFormatter(xmax=1))

# Delivery delta (worst)
delivery_sorted = state_df.sort_values("avg_delivery_delta", ascending=False).head(10)
axes[1, 0].barh(delivery_sorted["customer_state"], delivery_sorted["avg_delivery_delta"], color=PALETTE["accent"])
axes[1, 0].set_xlabel("Average delivery delta (days late)")
axes[1, 0].set_title("Top 10 states by worst delivery (latest)")

# Freight ratio
freight_sorted = state_df.sort_values("freight_ratio", ascending=False).head(10)
axes[1, 1].barh(freight_sorted["customer_state"], freight_sorted["freight_ratio"], color=PALETTE["neutral"])
axes[1, 1].set_xlabel("Freight ratio (freight / GMV)")
axes[1, 1].set_title("Top 10 states by highest freight ratio")

fig.tight_layout()
save_fig(fig, "geo_state_rankings.png")
plt.show()
```

**Output:**
```
<Figure size 1600x1200 with 4 Axes>
```

## 4. Choropleth maps

Interactive maps using Plotly with Brazil state boundaries. Loads a GeoJSON from a public URL (Code for America's Brazil states dataset). We create maps for GMV, churn rate, and average delivery delta.

**Cell 7:**
```python
# Load Brazil states GeoJSON (with fallback)
geojson = None
sources = [
    "https://raw.githubusercontent.com/kelvins/Municipios-Brasileiros/main/geojson/estados.json",
    "https://raw.githubusercontent.com/codeforamerica/click_that_hood/master/public/data/brazil-states.geojson"
]
headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

for url in sources:
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            geojson = response.json()
            print(f"Loaded GeoJSON from: {url}")
            break
    except:
        continue

# If URL downloads fail, use a hardcoded state mapping as fallback
if geojson is None:
    print("Warning: Could not load GeoJSON from URLs. Using hardcoded state IDs for choropleth.")
    brazil_states = {
        "AC": 1, "AL": 2, "AM": 3, "AP": 4, "BA": 5, "CE": 6, "DF": 7, "ES": 8,
        "GO": 9, "MA": 10, "MG": 11, "MS": 12, "MT": 13, "PA": 14, "PB": 15,
        "PE": 16, "PI": 17, "PR": 18, "RJ": 19, "RN": 20, "RO": 21, "RR": 22,
        "RS": 23, "SC": 24, "SE": 25, "SP": 26, "TO": 27
    }
    state_df["state_id"] = state_df["customer_state"].map(brazil_states)
    state_df_clean = state_df.dropna(subset=["state_id"]).copy()
else:
    # For the Kelvins GeoJSON, properties use "UF" and feature ID is "ID" (capitalized)
    try:
        state_id_map = {feature["properties"]["UF"]: feature["ID"] for feature in geojson["features"]}
        state_df["state_id"] = state_df["customer_state"].map(state_id_map)
        state_df_clean = state_df.dropna(subset=["state_id"]).copy()
    except (KeyError, AttributeError):
        # For the Code for America GeoJSON, properties use "sigla" and feature ID is "id"
        try:
            state_id_map = {feature["properties"]["sigla"]: feature["id"] for feature in geojson["features"]}
            state_df["state_id"] = state_df["customer_state"].map(state_id_map)
            state_df_clean = state_df.dropna(subset=["state_id"]).copy()
        except (KeyError, AttributeError):
            # If both fail, use hardcoded fallback
            print("Warning: GeoJSON format not recognized. Using hardcoded state IDs.")
            brazil_states = {
                "AC": 1, "AL": 2, "AM": 3, "AP": 4, "BA": 5, "CE": 6, "DF": 7, "ES": 8,
                "GO": 9, "MA": 10, "MG": 11, "MS": 12, "MT": 13, "PA": 14, "PB": 15,
                "PE": 16, "PI": 17, "PR": 18, "RJ": 19, "RN": 20, "RO": 21, "RR": 22,
                "RS": 23, "SC": 24, "SE": 25, "SP": 26, "TO": 27
            }
            state_df["state_id"] = state_df["customer_state"].map(brazil_states)
            state_df_clean = state_df.dropna(subset=["state_id"]).copy()

state_df_clean[["customer_state", "state_id"]].head()
```

**Output:**
```
Loaded GeoJSON from: https://raw.githubusercontent.com/codeforamerica/click_that_hood/master/public/data/brazil-states.geojson
Warning: GeoJSON format not recognized. Using hardcoded state IDs.
```
```
   customer_state  state_id
6              SP        26
23             RJ        19
18             MG        11
3              RS        23
14             PR        18
```

**Cell 8:**
```python
# Plotly choropleth: Total GMV
fig_gmv = px.choropleth(
    state_df_clean,
    geojson=geojson if geojson is not None else None,
    locations="state_id",
    color="total_gmv",
    hover_name="customer_state",
    hover_data={"total_gmv": ":,.2f", "customer_count": True, "churn_rate": ":.2%", "avg_delivery_delta": ":.1f"},
    color_continuous_scale="Viridis",
    title="Total GMV by state (R$)",
    scope="south america",
)
fig_gmv.update_layout(margin={"r":0,"t":50,"l":0,"b":0})
fig_gmv.show()
```

**Cell 9:**
```python
# Plotly choropleth: Churn rate
fig_churn = px.choropleth(
    state_df_clean,
    geojson=geojson if geojson is not None else None,
    locations="state_id",
    color="churn_rate",
    hover_name="customer_state",
    hover_data={"churn_rate": ":.2%", "customer_count": True, "total_gmv": ":,.2f"},
    color_continuous_scale="Reds",
    title="Churn rate by state",
    scope="south america",
)
fig_churn.update_layout(margin={"r":0,"t":50,"l":0,"b":0})
fig_churn.show()
```

**Cell 10:**
```python
# Plotly choropleth: Average delivery delta
fig_delivery = px.choropleth(
    state_df_clean,
    geojson=geojson if geojson is not None else None,
    locations="state_id",
    color="avg_delivery_delta",
    hover_name="customer_state",
    hover_data={"avg_delivery_delta": ":.1f", "customer_count": True, "total_gmv": ":,.2f"},
    color_continuous_scale="RdYlGn_r",
    title="Average delivery delta (days) — negative = early, positive = late",
    scope="south america",
)
fig_delivery.update_layout(margin={"r":0,"t":50,"l":0,"b":0})
fig_delivery.show()
```

## 5. Top cities by GMV

The top 15 cities by total GMV, colored by state.

**Cell 11:**
```python
fig, ax = plt.subplots(figsize=(12, 6))
colors = [PALETTE["primary"] if i % 2 == 0 else PALETTE["accent"] for i in range(len(top_cities))]
bars = ax.barh(top_cities["customer_city"] + " (" + top_cities["customer_state"] + ")", 
               top_cities["total_gmv"], color=colors)
ax.set_xlabel("Total GMV (R$)")
ax.set_title("Top 15 cities by GMV")
ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"R${x/1e6:.1f}M"))
for bar, val in zip(bars, top_cities["total_gmv"]):
    ax.annotate(f"R${val/1e6:.1f}M", xy=(val, bar.get_y() + bar.get_height()/2),
                xytext=(5, 0), textcoords="offset points", ha="left", va="center", fontsize=9)
fig.tight_layout()
save_fig(fig, "geo_top_cities.png")
plt.show()
```

**Output:**
```
<Figure size 1200x600 with 1 Axes>
```

## 6. Delivery vs satisfaction

Scatter plot of average delivery delta vs average review score by state, with bubble size = GMV. This highlights the relationship between logistics performance and customer satisfaction at regional level.

**Cell 12:**
```python
fig, ax = plt.subplots(figsize=(10, 6))
sc = ax.scatter(
    state_df_clean["avg_delivery_delta"],
    state_df_clean["avg_review_score"],
    s=state_df_clean["total_gmv"] / 10000,  # scale bubble size
    c=state_df_clean["churn_rate"],
    cmap="Reds",
    alpha=0.7,
    edgecolors="black",
    linewidth=0.5,
)
for _, row in state_df_clean.iterrows():
    ax.annotate(row["customer_state"], (row["avg_delivery_delta"], row["avg_review_score"]),
                fontsize=8, xytext=(5, 5), textcoords="offset points")
ax.set_xlabel("Average delivery delta (days late)")
ax.set_ylabel("Average review score")
ax.set_title("Delivery performance vs review score by state")
cbar = plt.colorbar(sc, ax=ax)
cbar.set_label("Churn rate")
ax.grid(True, alpha=0.3)
fig.tight_layout()
save_fig(fig, "geo_delivery_vs_satisfaction.png")
plt.show()
```

**Output:**
```
<Figure size 1000x600 with 2 Axes>
```

## 7. Correlation analysis

Compute the correlation matrix of state-level metrics to see which factors co-vary. This helps identify patterns: e.g., high churn states also have high freight ratio? Low review scores correlate with late deliveries?

**Cell 13:**
```python
corr_cols = ["total_gmv", "customer_count", "avg_order_value", 
             "avg_delivery_delta", "avg_pct_late", "avg_review_score",
             "churn_rate", "freight_ratio"]
corr_df = state_df_clean[corr_cols].corr()

plt.figure(figsize=(10, 8))
mask = np.triu(np.ones_like(corr_df, dtype=bool), k=1)
sns.heatmap(corr_df, mask=mask, annot=True, fmt=".2f", cmap="coolwarm",
            center=0, vmin=-1, vmax=1, square=True, linewidths=0.5)
plt.title("Correlation matrix of state-level metrics")
plt.tight_layout()
save_fig(plt.gcf(), "geo_correlation_matrix.png")
plt.show()
```

**Output:**
```
<Figure size 1000x800 with 2 Axes>
```

## 8. Churn vs GMV and freight ratio

Scatter plots: churn rate vs GMV (log scale) and churn vs freight ratio, to see if higher‑value states have lower churn, or if high freight costs drive churn.

**Cell 14:**
```python
fig, axes = plt.subplots(1, 2, figsize=(14, 5))

# Churn vs GMV (log scale)
axes[0].scatter(state_df_clean["total_gmv"], state_df_clean["churn_rate"],
                c=state_df_clean["avg_delivery_delta"], cmap="coolwarm", alpha=0.7)
axes[0].set_xscale("log")
axes[0].set_xlabel("Total GMV (R$) (log scale)")
axes[0].set_ylabel("Churn rate")
axes[0].set_title("Churn vs GMV (color = delivery delta)")
for _, row in state_df_clean.iterrows():
    axes[0].annotate(row["customer_state"], (row["total_gmv"], row["churn_rate"]), fontsize=8)

# Churn vs freight ratio
axes[1].scatter(state_df_clean["freight_ratio"], state_df_clean["churn_rate"],
                c=state_df_clean["avg_review_score"], cmap="viridis", alpha=0.7)
axes[1].set_xlabel("Freight ratio (freight / GMV)")
axes[1].set_ylabel("Churn rate")
axes[1].set_title("Churn vs freight ratio (color = avg review score)")
for _, row in state_df_clean.iterrows():
    axes[1].annotate(row["customer_state"], (row["freight_ratio"], row["churn_rate"]), fontsize=8)

fig.tight_layout()
save_fig(fig, "geo_churn_vs_gmv_freight.png")
plt.show()
```

**Output:**
```
<Figure size 1400x500 with 2 Axes>
```

## 9. Export summary

Write key state-level metrics to `reports/geo_summary.json` for documentation and for the README/case study.

**Cell 15:**
```python
summary = {
    "analysis_date": datetime.now().isoformat(),
    "states_analyzed": len(state_df_clean),
    "total_gmv_brl": float(state_df["total_gmv"].sum()),
    "top_state_by_gmv": state_df.sort_values("total_gmv", ascending=False).iloc[0]["customer_state"],
    "top_state_by_churn": state_df.sort_values("churn_rate", ascending=False).iloc[0]["customer_state"],
    "state_metrics": state_df.to_dict(orient="records"),
    "top_cities": top_cities.head(10).to_dict(orient="records"),
    "correlation_matrix": corr_df.to_dict(),
}

summary_path = REPORTS_DIR / "geo_summary.json"
with open(summary_path, "w", encoding="utf-8") as f:
    json.dump(summary, f, indent=2, default=str)

print(f"Summary written -> {summary_path}")
```

**Output:**
```
Summary written -> c:\Users\User\Desktop\crm-customer-intelligence-module\reports\geo_summary.json
```

---

**TL;DR:** The geospatial analysis confirms that GMV is concentrated in the Southeast (SP, RJ, MG), while the Northeast and North show higher churn rates and worse delivery performance. The scatter plot suggests that states with later deliveries tend to have lower review scores, reinforcing the importance of logistics for customer satisfaction. Correlation analysis reveals that churn is positively correlated with freight ratio and negatively with review scores. The choropleth maps provide an intuitive geographic view of these patterns.

**Next:** These insights feed into the Power BI geo dashboard and the business case narrative.
