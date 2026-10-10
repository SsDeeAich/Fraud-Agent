# Impact Compliance & Fraud Detection Agent - Evaluation Suite

This evaluation suite tests the autonomous Compliance Agent against the business logic defined in `prompt02` and grounded in the PCA Continuous Monitoring guidelines (`gs://impact_compliance-1/troubleshooting_pca_continuous_monitoring.pdf`).

---

## 🎯 Evaluation Dimensions & Weights

The LLM-as-a-Judge (`eval_rubrics.py`) grades agent investigation reports on a 1–5 scale across 5 core dimensions:

| Dimension | Weight | Criteria |
| :--- | :---: | :--- |
| **1. Entity & Graph Completeness** | 20% | Correctly identifies target publisher, domicile, business model, and **all linked accounts** from the graph. |
| **2. Threshold Adherence & Precision** | 25% | Validates 7D/30D/365D metrics and correctly checks conditional triggers **6A–6F** based on 30-day thresholds. Zero hallucinations. |
| **3. Allegation Reconciliation** | 20% | Reconciles advertiser allegations (e.g. Helium SDK spoofing) against internal PCA telemetry objectively. |
| **4. Domain Grounding** | 15% | Uses accurate PCA technical definitions (Click spamming `<0.01%` CVR, Click stuffing header anomalies `>80%`, User entropy repeat clusters). |
| **5. Actionability of Next Steps** | 20% | Provides progressive, actionable mitigation steps (payment holds, evidence requests, platform removal). |

**Pass Criteria**: Composite weighted score $\ge 4.0 / 5.0$ and no individual dimension $< 3$.

---

## 🧪 Evaluation Test Scenarios (`eval_dataset.json`)

1. **`EVAL-001-PROMPT02-BASELINE`**: Multi-account syndicate (AppTastic & AppTrust), critical risk score (~90), click spamming and stuffing. Reconciles Helium SDK exploit claim vs PCA telemetry. Triggers 6A and 6D.
2. **`EVAL-002-CLEAN-PUBLISHER`**: Legitimate Nordic cashback portal with low risk score (11.2). Verifies agent **avoids false positives** and does not recommend payment holds.
3. **`EVAL-003-GEO-CONTENT-ARBITRAGE`**: Cyprus content site with `High_risk_url_action_pct > 0.1` and `Geo_mismatch_pct > 0.5`. Triggers 6B, 6B1 (Copyscape/SEMrush), and 6C.
4. **`EVAL-004-BOTNET-CREDENTIAL-STUFFING`**: Multi-account ring with proxy logins (`>10%`) and disposable emails (`>10%`). Triggers 6D and 6E.
5. **`EVAL-005-ABNORMAL-PEAK-EWMA`**: Sharp volume rise relative to historical EWMA baseline. Triggers 6F.
6. **`EVAL-006-THRESHOLD-BOUNDARY-PRECISION`**: Edge-case mathematical boundaries (`Geo_mismatch_pct = 0.49`, `Click_spamming_pct = 0.11`). Verifies agent behaves accurately when no JIRA ticket ID is supplied.

---

## 🚀 Running Evals

Activate your virtual environment first:
```bash
source .venv/bin/activate
```

### 1. Fast Mock Evaluation (Offline / CI)
Evaluates deterministic trajectory rules and runs LLM-as-a-judge against the mock context:
```bash
python evals/run_evals.py --mock
```

### 2. Evaluate a Single Test Case
```bash
python evals/run_evals.py --mock --eval-id EVAL-001-PROMPT02-BASELINE
```

### 3. Fast Trajectory Checks Only (Skip LLM Judge)
```bash
python evals/run_evals.py --mock --skip-judge
```

### 4. Live Agent Evaluation (BigQuery & Local Agent)
Runs the live ADK `InMemoryRunner` agent with real BigQuery queries:
```bash
python evals/run_evals.py --live
```

---

## 📁 Artifacts
- **Dataset**: `evals/eval_dataset.json`
- **Rubrics & System Instructions**: `evals/eval_rubrics.py`
- **Runner Script**: `evals/run_evals.py`
- **Execution Results**: `evals/eval_results.json`
