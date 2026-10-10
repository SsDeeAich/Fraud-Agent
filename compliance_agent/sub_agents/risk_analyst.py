"""Risk Analyst Sub-Agent: Evaluates continuous monitoring metrics and executes conditional deep dives."""

import os
from google.adk.agents import LlmAgent
from google.adk.planners import BuiltInPlanner
from google.genai import types

from ..tools.bigquery_tools import (
    get_risk_scores,
    get_attribution_risk_details,
    get_url_risk_details,
    get_domain_intelligence,
    get_geo_mismatch_details,
    get_login_ip_risk_details,
    get_login_email_risk_details,
    get_abnormal_peaks_details,
)
from ..tools.gcs_tools import get_risk_signal_guidance

planner = BuiltInPlanner(
    thinking_config=types.ThinkingConfig(include_thoughts=True)
)

risk_analyst = LlmAgent(
    name="risk_analyst",
    model=os.getenv("AGENT_MODEL", "gemini-2.5-flash"),
    description="Specialized analyst for assessing PCA Continuous Monitoring scores across 7D/30D/365D intervals and executing deep-dive conditional risk analyses.",
    instruction="""You are the Risk Analyst specialist.
Your responsibility is to analyze continuous monitoring risk signals and perform targeted drilldowns:
1. Examine 7-day, 30-day, and 365-day risk metrics and PCA composite scores.
2. Trigger conditional deep dives based on 30-day threshold rules:
   - If long_cta_pct, Injected_click_pct, or Click_spamming_pct > 0.1: Check attribution risk (conversion rate, avg CTA, short CTA actions).
   - If High_risk_url_action_pct > 0.1: Check URL risk and drill down into SEMrush traffic intelligence.
   - If Geo_mismatch_pct > 0.5: Check geographical mismatch between publisher domicile, campaign region, and action locations.
   - If High_risk_login_ip_pct > 0.1: Check login IP anomalies (bot, proxy, hosting provider, crawler).
   - If High_risk_login_email_pct > 0.1: Check disposable/fraud email logins.
   - If Abnormal_peak_actions_pct or Abnormal_peak_clicks_pct > 0.1: Check sudden volume surges relative to baseline EWMA.
3. Consult the PCA Continuous Monitoring guidance tool to explain technical root causes, false positives, and diagnostic indicators.

Synthesize all quantitative risk findings with clear evidence-backed explanations.""",
    tools=[
        get_risk_scores,
        get_attribution_risk_details,
        get_url_risk_details,
        get_domain_intelligence,
        get_geo_mismatch_details,
        get_login_ip_risk_details,
        get_login_email_risk_details,
        get_abnormal_peaks_details,
        get_risk_signal_guidance,
    ],
    planner=planner,
)
