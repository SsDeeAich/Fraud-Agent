"""Account Analyst Sub-Agent: Gathers linked accounts, profile summaries, marketplace history, and JIRA records."""

import os
from google.adk.agents import LlmAgent
from google.adk.planners import BuiltInPlanner
from google.genai import types

from ..tools.bigquery_tools import (
    get_linked_accounts,
    get_publisher_summary,
    get_top_programs,
    get_marketplace_history,
    get_compliance_jira_history,
)

planner = BuiltInPlanner(
    thinking_config=types.ThinkingConfig(include_thoughts=True)
)

account_analyst = LlmAgent(
    name="account_analyst",
    model=os.getenv("AGENT_MODEL", "gemini-2.5-flash"),
    description="Specialized analyst for extracting and investigating account graphs, publisher entity details, marketplace approvals, and JIRA compliance history.",
    instruction="""You are the Account Analyst specialist.
Your responsibility is to investigate publisher account identity and lineage:
1. Identify all linked accounts in the account graph sharing common infrastructure, ownership, or registration signatures.
2. Extract publisher profile details (domicile, promo method, earnings, marketplace status, qualified-to-pay status).
3. Identify top program affiliations and brands.
4. Retrieve marketplace evaluation results and specific decline reasons.
5. Extract historical JIRA fraud complaints, advertiser reports, and compliance ticket commentary.

Summarize your findings clearly, highlighting linked entity clusters and historical violations.""",
    tools=[
        get_linked_accounts,
        get_publisher_summary,
        get_top_programs,
        get_marketplace_history,
        get_compliance_jira_history,
    ],
    planner=planner,
)
