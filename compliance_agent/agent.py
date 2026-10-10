"""Root supervisor agent module for the Compliance and Fraud Detection Agent."""

import json
import logging
import os
import re
from pathlib import Path
from dotenv import load_dotenv
from google.adk.apps import App
from google.adk.agents import LlmAgent
from google.adk.agents.context_cache_config import ContextCacheConfig
from google.adk.planners import BuiltInPlanner
from google.adk.tools.load_memory_tool import load_memory_tool
from google.adk.tools.preload_memory_tool import preload_memory_tool
from google.genai import types

from .tools.bigquery_tools import (
    get_linked_accounts,
    get_publisher_summary,
    get_top_programs,
    get_marketplace_history,
    get_compliance_jira_history,
    get_risk_scores,
    get_attribution_risk_details,
    get_url_risk_details,
    get_domain_intelligence,
    get_geo_mismatch_details,
    get_login_ip_risk_details,
    get_login_email_risk_details,
    get_abnormal_peaks_details,
    run_full_investigation_pipeline,
)
from .tools.gcs_tools import (
    upload_grounding_document,
    get_grounding_knowledge,
    get_risk_signal_guidance,
)
from .sub_agents import account_analyst, risk_analyst, report_writer
from .telemetry import setup_telemetry

# Initialize standard Python logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("compliance_agent")

# Load environment variables from .env file
env_path = Path(__file__).resolve().parent.parent / ".env"
if env_path.exists():
    load_dotenv(dotenv_path=env_path)
    logger.info("Loaded environment variables from %s", env_path)
else:
    load_dotenv()
    logger.info("Loaded environment variables from default environment / .env")

# Initialize OpenTelemetry Agent Observability (Cloud Trace / Spans)
setup_telemetry()

# Ensure valid SSL_CERT_FILE for requests
if not os.environ.get("SSL_CERT_FILE"):
    import certifi
    os.environ["SSL_CERT_FILE"] = certifi.where()

# Enable Thinking Mode using BuiltInPlanner
planner = BuiltInPlanner(
    thinking_config=types.ThinkingConfig(
        include_thoughts=True
    )
)

AGENT_INSTRUCTION = """You are the Lead Compliance & Fraud Detection Supervisor Agent.
Your responsibility is to orchestrate and execute end-to-end publisher fraud investigations following the 8-Step Compliance Workflow:

### Input Format:
Users may provide input such as:
- A publisher ID and optionally a JIRA ticket ID (e.g., "IRFRAUD-8254, 4381629", "Investigate publisher 4381629 for ticket IRFRAUD-8254", or "4381629").

### Investigation Workflow:
1. **Step 1 - Account Graph**: Call `get_linked_accounts` or run `run_full_investigation_pipeline` to uncover all linked accounts sharing ownership, device IDs, IPs, or payment structures.
2. **Steps 2-4 - Entity Profiles & History**:
   - Extract publisher metadata (domicile, promo method, marketplace status, GTV, active partnerships) with `get_publisher_summary` and `get_top_programs`.
   - Review automated onboarding/RTAS evaluation logs and decline reasons with `get_marketplace_history`.
   - Inspect historical JIRA fraud tickets, complaints, and investigator comments with `get_compliance_jira_history`.
3. **Step 5 - Continuous Monitoring Risk Analysis**:
   - Retrieve 7-day, 30-day, and 365-day risk scores and PCA metrics with `get_risk_scores`.
4. **Step 6 - Conditional Deep Dives**:
   - If `long_cta_pct`, `Injected_click_pct`, or `Click_spamming_pct` > 0.1: Run `get_attribution_risk_details`.
   - If `High_risk_url_action_pct` > 0.1: Run `get_url_risk_details` and check `get_domain_intelligence`.
   - If `Geo_mismatch_pct` > 0.5: Run `get_geo_mismatch_details`.
   - If `High_risk_login_ip_pct` > 0.1: Run `get_login_ip_risk_details`.
   - If `High_risk_login_email_pct` > 0.1: Run `get_login_email_risk_details`.
   - If `Abnormal_peak_actions_pct` or `Abnormal_peak_clicks_pct` > 0.1: Run `get_abnormal_peaks_details`.
   *(Note: `run_full_investigation_pipeline` executes Steps 1 through 7 automatically in parallel and returns the complete consolidated JSON object).*
5. **Step 8 - Grounded Summary Generation**:
   - Synthesize the consolidated findings into a comprehensive, executive-ready fraud investigation report grounded by PCA Continuous Monitoring guidelines (`gs://impact_compliance-1/troubleshooting_pca_continuous_monitoring.pdf`).
   - Structure the output into clear sections:
     * Entity Identification & Linked Accounts
     * Platform & Commercial History
     * Historical Complaints (JIRA & Advertisers)
     * PCA Continuous Monitoring & Specific Fraud Indicators (Click spamming, Click stuffing, User entropy, Geo mismatch)
     * Assessment of Current Ticket Allegations
      * Actionable Next Steps (Payment holds, evidence requests, marketplace/platform removal).
6. **Agent Platform Memory Bank**:
   - You are equipped with the Agent Platform Memory Bank via `preload_memory_tool` (automatically preloads relevant prior notes for the entity) and `load_memory_tool` (call explicitly with queries like 'previous notes for publisher 4381629').
   - Use memory to maintain continuity across investigations and recall past compliance decisions.

Always reason through the evidence step-by-step and provide detailed, professional fraud reports."""

# Root supervisor agent definition using ADK LlmAgent
root_agent = LlmAgent(
    name="compliance_agent",
    model=os.getenv("AGENT_MODEL", "gemini-2.5-flash"),
    description="Root supervisor agent for compliance, fraud detection, and regulatory oversight.",
    instruction=AGENT_INSTRUCTION,
    tools=[
        run_full_investigation_pipeline,
        get_linked_accounts,
        get_publisher_summary,
        get_top_programs,
        get_marketplace_history,
        get_compliance_jira_history,
        get_risk_scores,
        get_attribution_risk_details,
        get_url_risk_details,
        get_domain_intelligence,
        get_geo_mismatch_details,
        get_login_ip_risk_details,
        get_login_email_risk_details,
        get_abnormal_peaks_details,
        get_grounding_knowledge,
        get_risk_signal_guidance,
        upload_grounding_document,
        load_memory_tool,
        preload_memory_tool,
    ],
    sub_agents=[
        account_analyst,
        risk_analyst,
        report_writer,
    ],
    planner=planner,
)

# KV Cache / Context Caching Configuration (Gemini Context Cache)
# Caches prompt prefix & multi-turn investigation history, reducing latency and costs.
context_cache_config = ContextCacheConfig(
    ttl_seconds=int(os.getenv("CONTEXT_CACHE_TTL_SECONDS", "3600")),
    min_tokens=int(os.getenv("CONTEXT_CACHE_MIN_TOKENS", "2048")),
    cache_intervals=int(os.getenv("CONTEXT_CACHE_INTERVALS", "1")),
)

# Exported ADK App wrapping the root agent with KV Cache and Memory Bank
app = App(
    name="compliance_agent",
    root_agent=root_agent,
    context_cache_config=context_cache_config,
)
