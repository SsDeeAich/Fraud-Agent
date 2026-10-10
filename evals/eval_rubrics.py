"""Evaluation rubrics and LLM-as-a-Judge definitions for the Compliance Agent."""

from typing import List, Optional
from pydantic import BaseModel, Field


class JudgeScoreDetails(BaseModel):
    entity_and_graph_completeness: int = Field(
        ...,
        description="Score 1-5: Identification of primary publisher, domicile, promo method, and all linked account IDs from the graph.",
        ge=1,
        le=5,
    )
    threshold_adherence_and_precision: int = Field(
        ...,
        description="Score 1-5: Correct representation of 7D/30D/365D scores and accurate triggering/quoting of Step 6 conditional deep-dives.",
        ge=1,
        le=5,
    )
    allegation_reconciliation: int = Field(
        ...,
        description="Score 1-5: Objective fact-checking of JIRA ticket claims against telemetry (e.g. injected clicks vs PCA flags, unique IPs).",
        ge=1,
        le=5,
    )
    domain_grounding: int = Field(
        ...,
        description="Score 1-5: Accurate grounding in PCA guidelines (click spamming <0.01% CVR, header anomalies >80%, user entropy device clusters).",
        ge=1,
        le=5,
    )
    actionability_of_next_steps: int = Field(
        ...,
        description="Score 1-5: Clear, progressive, numbered next steps (qualified-to-pay hold, evidence requests, marketplace/platform removal).",
        ge=1,
        le=5,
    )


class EvalJudgeVerdict(BaseModel):
    eval_id: str
    scores: JudgeScoreDetails
    weighted_score: float = Field(
        ...,
        description="Weighted composite score out of 5.0 (Weights: Entity 20%, Thresholds 25%, Allegation 20%, Grounding 15%, Actions 20%).",
    )
    passed: bool = Field(
        ...,
        description="True if weighted_score >= 4.0 and no individual score is below 3, else False.",
    )
    critical_flaws: List[str] = Field(
        default_factory=list,
        description="List of factual hallucinations, missing linked accounts, or faulty conclusions.",
    )
    judge_feedback: str = Field(
        ...,
        description="Comprehensive narrative justification explaining the scores assigned.",
    )


JUDGE_SYSTEM_INSTRUCTION = """You are a Principal Compliance Officer and Executive LLM-as-a-Judge.
Your mandate is to evaluate fraud investigation reports produced by an autonomous Compliance AI Agent.

### Grading Scale (1 to 5):
1. **Entity & Graph Completeness (Weight: 20%)**:
   - 5: Explicitly names primary publisher ID, name, country, promo method, and ALL linked account IDs from the graph.
   - 3: Mentions primary publisher and some linked accounts, but misses entity relationships or country context.
   - 1: Evaluates only the single primary ID and completely omits the account graph.

2. **Threshold Adherence & Finding Precision (Weight: 25%)**:
   - 5: Accurately reflects 7D, 30D, and 365D risk metrics and correctly cites metrics for triggered conditional deep-dives (6A-6F). Never hallucinates metrics for unflagged signals.
   - 3: Mentions overall risk level (critical/elevated), but confuses timeframes or misquotes percentage numbers.
   - 1: Hallucinates metrics or flags signals that were never in the data.

3. **Allegation Rebuttal & Telemetry Reconciliation (Weight: 20%)**:
   - 5: Directly addresses the JIRA ticket allegation and performs an objective fact-check against telemetry (e.g. distinguishing alleged injected clicks from actual PCA confirmation; recognizing that unique IPs alone are not proof of spoofing).
   - 3: Mentions the ticket allegations, but uncritically accepts advertiser claims without verifying against data.
   - 1: Ignores the JIRA allegations completely or gives an unfounded conclusion.

4. **Domain Grounding & PCA Terminology (Weight: 15%)**:
   - 5: Flawlessly applies definitions from the PCA troubleshooting guide (Click spamming < 0.01% CVR, Click stuffing header anomalies > 80%, User entropy repeat clusters, EWMA baselines).
   - 3: Uses generic fraud words without tying them to specific PCA technical definitions.
   - 1: Confuses terms (e.g. confusing click spamming with click stuffing).

5. **Actionability of Next Steps (Weight: 20%)**:
   - 5: Delivers numbered, progressive, specific actions (qualified-to-pay hold, confirming short CTA timestamps, requesting brand SDK logs, platform/marketplace removal).
   - 3: Gives vague advice (e.g. "continue monitoring", "be careful").
   - 1: Proposes unhelpful or completely disproportionate actions.

Return your evaluation strictly in the requested JSON structure."""
