"""Asynchronous BigQuery extraction and analytics tools for the Compliance Agent."""

import asyncio
import datetime
import logging
import os
from typing import Any, Dict, List, Optional
from google.cloud import bigquery

from ..telemetry import trace_tool, trace_span

logger = logging.getLogger("compliance_agent.bigquery_tools")

PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT", "customers-444618")
DATASET_ID = os.getenv("BIGQUERY_DATASET_ID", "compliance_agent")

def _get_client() -> bigquery.Client:
    return bigquery.Client(project=PROJECT_ID)

def _serialize_row(row_dict: dict) -> dict:
    """Helper to convert datetime/timestamp/date objects to strings."""
    clean = {}
    for k, v in row_dict.items():
        if isinstance(v, (datetime.datetime, datetime.date)):
            clean[k] = v.isoformat()
        else:
            clean[k] = v
    return clean


@trace_tool(tool_type="bigquery")
async def get_linked_accounts(publisher_id: int) -> List[int]:
    """Finds all account_ids linked in the same fraud/account graph as the publisher_id (Step 1).

    Args:
        publisher_id: The publisher account ID to investigate.

    Returns:
        List of linked unique account IDs including the input publisher ID.
    """
    def _query() -> List[int]:
        client = _get_client()
        query = f"""
        WITH graph AS (
          SELECT graph_id
          FROM `{PROJECT_ID}.{DATASET_ID}.account_graph_main_20251106`
          WHERE account_id = @publisher_id
        )
        SELECT DISTINCT ag.account_id
        FROM `{PROJECT_ID}.{DATASET_ID}.account_graph_main_20251106` ag
        JOIN graph g ON ag.graph_id = g.graph_id;
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("publisher_id", "INT64", publisher_id)]
        )
        try:
            results = [row.account_id for row in client.query(query, job_config=job_config).result()]
            if not results or publisher_id not in results:
                results = list(set([publisher_id] + results))
            return results
        except Exception as e:
            logger.error(f"Error querying linked accounts for {publisher_id}: {e}")
            return [publisher_id]

    return await asyncio.to_thread(_query)


@trace_tool(tool_type="bigquery")
async def get_publisher_summary(account_list: List[int]) -> List[Dict[str, Any]]:
    """Gets publisher profile details for the accounts in account_list (Step 2.1).

    Args:
        account_list: List of publisher IDs.

    Returns:
        List of publisher details.
    """
    if not account_list:
        return []

    def _query() -> List[Dict[str, Any]]:
        client = _get_client()
        query = f"""
        SELECT publisher_id, publisher_name, publisher_description, primary_promo_method,
               company_url, country, account_status, account_create_date as account_creation_date,
               mkpl_status as marketplace_status, mkpl_approve_date as marketplace_approval_date,
               publisher_rating, finance_qualify_to_pay_status as is_qualified_to_pay,
               lifetime_gtv, last_30_days_yoy_gtv as last_30_day_day_gtv,
               top_ref_domain1 as Top1_ref_domain, last_30_days_click_active_partnership_count as partnership_count
        FROM `{PROJECT_ID}.{DATASET_ID}.publisher_20251106`
        WHERE publisher_id IN UNNEST(@account_list);
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ArrayQueryParameter("account_list", "INT64", account_list)]
        )
        return [_serialize_row(dict(row)) for row in client.query(query, job_config=job_config).result()]

    return await asyncio.to_thread(_query)


@trace_tool(tool_type="bigquery")
async def get_top_programs(account_list: List[int]) -> List[Dict[str, Any]]:
    """Gets top programs / brand partnerships by GTV for account_list (Step 2.2).

    Args:
        account_list: List of publisher IDs.

    Returns:
        List of top programs per publisher.
    """
    if not account_list:
        return []

    def _query() -> List[Dict[str, Any]]:
        client = _get_client()
        query = f"""
        WITH raw AS (
          SELECT publisher_id, campaign_name as program_name, lifetime_gtv,
          ROW_NUMBER() OVER (PARTITION BY publisher_id ORDER BY lifetime_gtv DESC) AS gtv_rank
          FROM `{PROJECT_ID}.{DATASET_ID}.partnership_affiliation`
          WHERE publisher_id IN UNNEST(@account_list)
        )
        SELECT publisher_id, STRING_AGG(program_name ORDER BY gtv_rank LIMIT 10) as top_programs
        FROM raw
        WHERE gtv_rank <= 10
        GROUP BY 1;
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ArrayQueryParameter("account_list", "INT64", account_list)]
        )
        return [_serialize_row(dict(row)) for row in client.query(query, job_config=job_config).result()]

    return await asyncio.to_thread(_query)


@trace_tool(tool_type="bigquery")
async def get_marketplace_history(account_list: List[int]) -> List[Dict[str, Any]]:
    """Retrieves marketplace application and automated evaluation history (Step 3).

    Args:
        account_list: List of publisher IDs.

    Returns:
        Evaluation results and decline reasons from RTAS and IRAM scored tables.
    """
    if not account_list:
        return []

    def _query() -> List[Dict[str, Any]]:
        client = _get_client()
        query = f"""
        SELECT publisher_id, request_context__title as publisher_name, TIMESTAMP(application_evaluation_date) as application_date, result_state as evaluation,
               TRIM(CONCAT(
                 IF(evaluations__profanity_check__result = "FAILED", "profanity,", ""),
                 IF(evaluations__duplicate_device_id__result = "FAILED", "duplicate_device,", ""),
                 IF(evaluations__unique_media_properties__result = "FAILED", "non_unique_media_property,", ""),
                 IF(evaluations__email_domain__result = "FAILED", "high_risk_email_domain,", ""),
                 IF(evaluations__email_risk__result = "FAILED", "high_risk_email,", ""),
                 IF(evaluations__ip_risk__result = "FAILED", "high_risk_ip,", ""),
                 IF(evaluations__url_risk__result = "FAILED", "high_risk_url,", ""),
                 IF(evaluations__geo_risk__result = "FAILED", "high_risk_geo,", ""),
                 IF(evaluations__fake_audience__result = "FAILED", "fake_audience,", ""),
                 IF(evaluations__offensive_text__result = "FAILED", "offensive_text,", ""),
                 IF(evaluations__offensive_image__result = "FAILED", "offensive_image", "")
               ), ',') as decline_reasons
        FROM `{PROJECT_ID}.{DATASET_ID}.rtas_results`
        WHERE publisher_id IN UNNEST(@account_list)
        UNION ALL
        SELECT id as publisher_id, organizationname as publisher_name, TIMESTAMP(platform_application_date) as application_date, prediction as evaluation, decline_reasons
        FROM `{PROJECT_ID}.{DATASET_ID}.iram_application_scored_20251106`
        WHERE id IN UNNEST(@account_list);
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ArrayQueryParameter("account_list", "INT64", account_list)]
        )
        return [_serialize_row(dict(row)) for row in client.query(query, job_config=job_config).result()]

    return await asyncio.to_thread(_query)


@trace_tool(tool_type="bigquery")
async def get_compliance_jira_history(account_list: List[int]) -> List[Dict[str, Any]]:
    """Retrieves JIRA fraud tickets, summaries, status, and comments (Step 4).

    Args:
        account_list: List of publisher IDs.

    Returns:
        List of JIRA compliance issues and comments.
    """
    if not account_list:
        return []

    def _query() -> List[Dict[str, Any]]:
        client = _get_client()
        query = f"""
        SELECT r.issue_id, r.partner_id, r.created_date, r.summary, r.description, r.status, r.status_changed,
               c.author as commenter, c.body as comment, c.created as comment_created
        FROM `{PROJECT_ID}.{DATASET_ID}.raw_jira_20251106` r
        LEFT JOIN `{PROJECT_ID}.{DATASET_ID}.comments_20251106` c ON r.issue_id = c.issue
        WHERE EXISTS (
          SELECT 1
          FROM UNNEST(SPLIT(r.partner_id, " ")) pid
          WHERE SAFE_CAST(REGEXP_EXTRACT(pid, "[0-9]+") AS INT64) IN UNNEST(@account_list)
        );
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ArrayQueryParameter("account_list", "INT64", account_list)]
        )
        return [_serialize_row(dict(row)) for row in client.query(query, job_config=job_config).result()]

    return await asyncio.to_thread(_query)


@trace_tool(tool_type="bigquery")
async def get_risk_scores(account_list: List[int], primary_publisher_id: int, date_interval_days: int = 30) -> List[Dict[str, Any]]:
    """Retrieves continuous monitoring risk metrics and PCA scores (Step 5).

    Args:
        account_list: List of publisher IDs.
        primary_publisher_id: The primary input publisher ID.
        date_interval_days: 7, 30, or 365.

    Returns:
        Aggregated continuous monitoring metrics.
    """
    if not account_list:
        return []

    def _query() -> List[Dict[str, Any]]:
        client = _get_client()
        query = f"""
        SELECT publisher_id, publisher_name, IF(publisher_id = @publisher_ID, 1, 0) as primary,
               AVG(Actions_7days) as avg_weekly_actions, AVG(Clicks_7days) as avg_weekly_clicks, AVG(Commissions_7days) as avg_weekly_commissions,
               SAFE_DIVIDE(SUM(Long_cta_pct * actions_7days), SUM(actions_7days)) as long_cta_pct,
               SAFE_DIVIDE(SUM(Injected_click_pct * actions_7days), SUM(actions_7days)) as Injected_click_pct,
               SAFE_DIVIDE(SUM(Click_spamming_pct * actions_7days), SUM(actions_7days)) as Click_spamming_pct,
               SAFE_DIVIDE(SUM(Cs_clicks_pct * clicks_7days ), SUM(clicks_7days)) as cs_clicks_pct,
               SAFE_DIVIDE(SUM(Cs_actions_pct * actions_7days ), SUM(actions_7days)) as cs_actions_pct,
               SAFE_DIVIDE(SUM(Rejection_rate_7days * actions_7days ), SUM(actions_7days)) as Rejection_rate_7days,
               SAFE_DIVIDE(SUM(High_risk_url_action_pct * actions_7days ), SUM(actions_7days)) as High_risk_url_action_pct,
               SAFE_DIVIDE(SUM(Geo_mismatch_pct * actions_7days ), SUM(actions_7days)) as Geo_mismatch_pct,
               SAFE_DIVIDE(SUM(High_risk_login_ip_pct * total_logins_7day ), SUM(total_logins_7day)) as High_risk_login_ip_pct,
               SAFE_DIVIDE(SUM(High_risk_login_email_pct * total_logins_7day ), SUM(total_logins_7day)) as High_risk_login_email_pct,
               SAFE_DIVIDE(SUM(Abnormal_peak_actions_pct * actions_7days ), SUM(actions_7days)) as Abnormal_peak_actions_pct,
               SAFE_DIVIDE(SUM(Abnormal_peak_clicks_pct * clicks_7days ), SUM(clicks_7days)) as Abnormal_peak_clicks_pct,
               SAFE_DIVIDE(SUM(Prop_actions_outside * actions_7days ), SUM(actions_7days)) as Prop_actions_outside,
               SAFE_DIVIDE(SUM(Prop_clicks_outside * clicks_7days ), SUM(clicks_7days)) as Prop_clicks_outside,
               SAFE_DIVIDE(SUM(Prop_actions_no_refurl * actions_7days ), SUM(actions_7days)) as Prop_actions_no_refurl,
               AVG(User_entropy_risk) as avg_user_entropy,
               SAFE_DIVIDE(SUM(Rc_proxy_risk * clicks_7days), SUM(clicks_7days)) as Rc_proxy_risk,
               SAFE_DIVIDE(SUM(Rc_ip_reputation_risk * clicks_7days), SUM(clicks_7days)) as Rc_ip_reputation_risk,
               SAFE_DIVIDE(SUM(Rc_data_center_risk * clicks_7days), SUM(clicks_7days)) as Rc_data_center_risk,
               AVG(Final_score) as avg_final_score,
               SAFE_DIVIDE(COUNTIF(risk_level = "critical"), COUNT(*)) as critical_flagged_pct,
               SAFE_DIVIDE(COUNTIF(risk_level = "elevated"), COUNT(*)) as elevated_flagged_pct,
               SAFE_DIVIDE(COUNTIF(risk_level = "mild"), COUNT(*)) as mild_flagged_pct
        FROM `{PROJECT_ID}.{DATASET_ID}.partner_continuous_monitoring_final_scores_20251106`
        WHERE publisher_id IN UNNEST(@account_list)
        GROUP BY 1, 2, 3;
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ArrayQueryParameter("account_list", "INT64", account_list),
                bigquery.ScalarQueryParameter("publisher_ID", "INT64", primary_publisher_id),
            ]
        )
        return [_serialize_row(dict(row)) for row in client.query(query, job_config=job_config).result()]

    return await asyncio.to_thread(_query)


@trace_tool(tool_type="bigquery")
async def get_attribution_risk_details(account_id: int) -> List[Dict[str, Any]]:
    """Retrieves conversion rate, average CTA, and short CTA actions (Step 6A).

    Args:
        account_id: Publisher ID to query.

    Returns:
        Attribution risk details per campaign.
    """
    def _query() -> List[Dict[str, Any]]:
        client = _get_client()
        query = f"""
        SELECT publisher_id, campaign_id,
               SAFE_DIVIDE(SUM(actions), SUM(clicks)) as conversion_rate,
               SAFE_DIVIDE(SUM(Avg_cta * actions), SUM(actions)) as avg_cta,
               SAFE_DIVIDE(SUM(Perc_actions_lt_30sec * actions), SUM(actions)) as Perc_actions_lt_30sec
        FROM `{PROJECT_ID}.{DATASET_ID}.attribution_risk_new`
        WHERE publisher_id = @current_account_id
        GROUP BY 1, 2;
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("current_account_id", "INT64", account_id)]
        )
        return [_serialize_row(dict(row)) for row in client.query(query, job_config=job_config).result()]

    return await asyncio.to_thread(_query)


@trace_tool(tool_type="bigquery")
async def get_url_risk_details(account_list: List[int]) -> List[Dict[str, Any]]:
    """Retrieves high risk URL actions and breakdown of reasons (Step 6B).

    Args:
        account_list: List of high risk URL publisher IDs.

    Returns:
        URL risk metrics by publisher and domain.
    """
    if not account_list:
        return []

    def _query() -> List[Dict[str, Any]]:
        client = _get_client()
        query = f"""
        SELECT publisher_id, domain, SUM(num_actions) as num_actions,
               SUM(IF(rc_url_abnormal_bounce_rate OR rc_url_abnormal_traffic_source OR rc_url_copied_content OR rc_url_low_visit OR rc_url_low_visit_duration OR rc_url_low_word_count, 1, 0) * num_actions) as flagged_actions,
               SUM(IF(rc_url_abnormal_bounce_rate, 1, 0) * num_actions) as rc_url_abnormal_bounce_rate,
               SUM(IF(rc_url_abnormal_traffic_source, 1, 0) * num_actions) as rc_url_abnormal_traffic_source,
               SUM(IF(rc_url_copied_content, 1, 0) * num_actions) as rc_url_copied_content,
               SUM(IF(rc_url_low_visit, 1, 0) * num_actions) as rc_url_low_visit,
               SUM(IF(rc_url_low_visit_duration, 1, 0) * num_actions) as rc_url_low_visit_duration,
               SUM(IF(rc_url_low_word_count, 1, 0) * num_actions) as rc_url_low_word_count
        FROM `{PROJECT_ID}.{DATASET_ID}.url_risk_monitoring_20251106`
        WHERE publisher_id IN UNNEST(@high_risk_url_accounts)
        GROUP BY 1, 2
        ORDER BY flagged_actions DESC;
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ArrayQueryParameter("high_risk_url_accounts", "INT64", account_list)]
        )
        return [_serialize_row(dict(row)) for row in client.query(query, job_config=job_config).result()]

    return await asyncio.to_thread(_query)


@trace_tool(tool_type="bigquery")
async def get_domain_intelligence(domains: List[str]) -> List[Dict[str, Any]]:
    """Retrieves SEMrush traffic intelligence for flagged domains (Step 6B1).

    Args:
        domains: List of domain strings.

    Returns:
        Traffic summary, bounce rate, traffic source metrics.
    """
    if not domains:
        return []

    def _query() -> List[Dict[str, Any]]:
        client = _get_client()
        query = f"""
        SELECT url, traffic_summary_visits, traffic_summary_bounce_rate, traffic_summary_desktop_bounce_rate,
               traffic_summary_time_on_site, traffic_summary_paid, traffic_summary_referral, traffic_summary_social_paid,
               traffic_summary_direct, traffic_summary_display_ad, traffic_summary_mail, traffic_summary_search,
               traffic_summary_social_organic
        FROM `{PROJECT_ID}.{DATASET_ID}.intelligence_semrush_operator_only_20251106`
        WHERE url IN UNNEST(@flagged_domains);
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ArrayQueryParameter("flagged_domains", "STRING", domains)]
        )
        return [_serialize_row(dict(row)) for row in client.query(query, job_config=job_config).result()]

    return await asyncio.to_thread(_query)


@trace_tool(tool_type="bigquery")
async def get_geo_mismatch_details(account_list: List[int]) -> List[Dict[str, Any]]:
    """Retrieves geo mismatch signals between publisher and action regions (Step 6C).

    Args:
        account_list: List of publisher IDs with high geo mismatch.

    Returns:
        Geo breakdown and action counts.
    """
    if not account_list:
        return []

    def _query() -> List[Dict[str, Any]]:
        client = _get_client()
        query = f"""
        SELECT publisher_id, publisher_name, campaign_id, action_region, publisher_promotional_region,
               publisher_region, campaign_region, campaign_main_region, campaign_shipping_region,
               ROUND(SUM(total_actions_7day)/7) as total_actions
        FROM `{PROJECT_ID}.{DATASET_ID}.geo_mismatch_monitoring_20251106`
        WHERE publisher_id IN UNNEST(@geo_mismatch_accounts)
        GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9
        ORDER BY total_actions DESC;
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ArrayQueryParameter("geo_mismatch_accounts", "INT64", account_list)]
        )
        return [_serialize_row(dict(row)) for row in client.query(query, job_config=job_config).result()]

    return await asyncio.to_thread(_query)


@trace_tool(tool_type="bigquery")
async def get_login_ip_risk_details(account_list: List[int]) -> List[Dict[str, Any]]:
    """Retrieves suspicious login IP anomalies, bot/proxy flags (Step 6D).

    Args:
        account_list: List of account IDs with high login IP risk.

    Returns:
        Login counts and risk proportions per IP.
    """
    if not account_list:
        return []

    def _query() -> List[Dict[str, Any]]:
        client = _get_client()
        query = f"""
        SELECT account_id, ipaddr, COUNT(*) as logins,
               SAFE_DIVIDE(COUNTIF(high_risk_ip = "yes"), COUNT(*)) as high_risk_logins,
               SAFE_DIVIDE(COUNTIF(rc_ip_bot_activity), COUNT(*)) as bot_activity,
               SAFE_DIVIDE(COUNTIF(rc_ip_crawler), COUNT(*)) as crawler,
               SAFE_DIVIDE(COUNTIF(rc_ip_hosting_provider), COUNT(*)) as hosting_provider,
               SAFE_DIVIDE(COUNTIF(rc_ip_proxy), COUNT(*)) as proxy
        FROM `{PROJECT_ID}.{DATASET_ID}.login_ip_risk_monitoring_20251106`
        WHERE account_id IN UNNEST(@high_risk_ip_accounts)
        GROUP BY 1, 2
        ORDER BY logins DESC;
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ArrayQueryParameter("high_risk_ip_accounts", "INT64", account_list)]
        )
        return [_serialize_row(dict(row)) for row in client.query(query, job_config=job_config).result()]

    return await asyncio.to_thread(_query)


@trace_tool(tool_type="bigquery")
async def get_login_email_risk_details(account_list: List[int]) -> List[Dict[str, Any]]:
    """Retrieves disposable email / email fraud risk indicators (Step 6E).

    Args:
        account_list: List of account IDs with high login email risk.

    Returns:
        Login email risk breakdown.
    """
    if not account_list:
        return []

    def _query() -> List[Dict[str, Any]]:
        client = _get_client()
        query = f"""
        SELECT account_id, email, COUNT(*) as logins,
               SAFE_DIVIDE(COUNTIF(rc_email_disposable), COUNT(*)) as rc_email_disposable,
               SAFE_DIVIDE(COUNTIF(rc_email_domain_risk), COUNT(*)) as rc_email_domain_risk,
               SAFE_DIVIDE(COUNTIF(rc_email_fraud), COUNT(*)) as rc_email_fraud
        FROM `{PROJECT_ID}.{DATASET_ID}.login_email_risk_monitoring_20251106`
        WHERE account_id IN UNNEST(@high_risk_email_accounts)
        GROUP BY 1, 2
        ORDER BY logins DESC;
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ArrayQueryParameter("high_risk_email_accounts", "INT64", account_list)]
        )
        return [_serialize_row(dict(row)) for row in client.query(query, job_config=job_config).result()]

    return await asyncio.to_thread(_query)


@trace_tool(tool_type="bigquery")
async def get_abnormal_peaks_details(account_list: List[int]) -> List[Dict[str, Any]]:
    """Retrieves abnormal traffic surges and spikes above expected baseline (Step 6F).

    Args:
        account_list: List of publisher IDs with abnormal peaks.

    Returns:
        Spike details per publisher and campaign.
    """
    if not account_list:
        return []

    def _query() -> List[Dict[str, Any]]:
        client = _get_client()
        query = f"""
        SELECT publisher_id, campaign_id, actions_ewma6 as expected_actions, num_actions,
               perc_rise_actions, cam_perc_rise_actions, clicks_ewma6 as expected_clicks,
               num_clicks, perc_rise_clicks, cam_perc_rise_clicks
        FROM `{PROJECT_ID}.{DATASET_ID}.abnormal_peak_monitoring_20251106`
        WHERE publisher_id IN UNNEST(@abnormal_peak_accounts)
        ORDER BY num_actions DESC;
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ArrayQueryParameter("abnormal_peak_accounts", "INT64", account_list)]
        )
        return [_serialize_row(dict(row)) for row in client.query(query, job_config=job_config).result()]

    return await asyncio.to_thread(_query)

@trace_tool(name="pipeline.run_full_investigation_pipeline", tool_type="pipeline")
async def run_full_investigation_pipeline(publisher_id: int, jira_ticket_id: str = "") -> Dict[str, Any]:
    """Asynchronously executes the complete multi-step fraud detection and compliance data extraction pipeline (Steps 1-7).

    Args:
        publisher_id: The primary publisher account ID to investigate.
        jira_ticket_id: Optional JIRA ticket ID for context.

    Returns:
        Consolidated JSON dictionary containing all investigation data keyed by publisher_id.
    """
    logger.info("Starting async full investigation pipeline for publisher_id=%s, jira_ticket_id=%s", publisher_id, jira_ticket_id)

    # Step 1: Identify Linked Accounts
    with trace_span("pipeline.step_1_account_graph", {"publisher_id": publisher_id, "jira_ticket_id": jira_ticket_id}):
        account_list = await get_linked_accounts(publisher_id)
        logger.info("Step 1 complete. Linked accounts found: %s", account_list)

    # Steps 2-4 & 5: Parallel Asynchronous Data Extraction
    with trace_span("pipeline.steps_2_to_5_data_extraction", {"account_count": len(account_list)}):
        (
            summaries,
            top_programs_raw,
            mkpl_history,
            jira_history,
            risk_7d,
            risk_30d,
            risk_365d,
        ) = await asyncio.gather(
            get_publisher_summary(account_list),
            get_top_programs(account_list),
            get_marketplace_history(account_list),
            get_compliance_jira_history(account_list),
            get_risk_scores(account_list, publisher_id, 7),
            get_risk_scores(account_list, publisher_id, 30),
            get_risk_scores(account_list, publisher_id, 365),
        )

    # Organize Step 5 results by publisher_id
    risk_by_pub_7d = {r["publisher_id"]: r for r in risk_7d}
    risk_by_pub_30d = {r["publisher_id"]: r for r in risk_30d}
    risk_by_pub_365d = {r["publisher_id"]: r for r in risk_365d}

    # Step 6: Identify which conditional deep dives to trigger based on 30-day risk metrics
    attr_risk_accounts = []
    high_risk_url_accounts = []
    geo_mismatch_accounts = []
    high_risk_ip_accounts = []
    high_risk_email_accounts = []
    abnormal_peak_accounts = []

    for pub_id in account_list:
        r30 = risk_by_pub_30d.get(pub_id, {})
        if (
            (r30.get("long_cta_pct") or 0) > 0.1
            or (r30.get("Injected_click_pct") or 0) > 0.1
            or (r30.get("Click_spamming_pct") or 0) > 0.1
        ):
            attr_risk_accounts.append(pub_id)
        if (r30.get("High_risk_url_action_pct") or 0) > 0.1:
            high_risk_url_accounts.append(pub_id)
        if (r30.get("Geo_mismatch_pct") or 0) > 0.5:
            geo_mismatch_accounts.append(pub_id)
        if (r30.get("High_risk_login_ip_pct") or 0) > 0.1:
            high_risk_ip_accounts.append(pub_id)
        if (r30.get("High_risk_login_email_pct") or 0) > 0.1:
            high_risk_email_accounts.append(pub_id)
        if (
            (r30.get("Abnormal_peak_actions_pct") or 0) > 0.1
            or (r30.get("Abnormal_peak_clicks_pct") or 0) > 0.1
        ):
            abnormal_peak_accounts.append(pub_id)

    # Execute conditional Step 6 queries in parallel asynchronously
    with trace_span(
        "pipeline.step_6_conditional_deep_dives",
        {
            "attr_risk_accounts": len(attr_risk_accounts),
            "high_risk_url_accounts": len(high_risk_url_accounts),
            "geo_mismatch_accounts": len(geo_mismatch_accounts),
            "high_risk_ip_accounts": len(high_risk_ip_accounts),
            "high_risk_email_accounts": len(high_risk_email_accounts),
            "abnormal_peak_accounts": len(abnormal_peak_accounts),
        },
    ):
        attr_tasks = [get_attribution_risk_details(pid) for pid in attr_risk_accounts]
        url_task = get_url_risk_details(high_risk_url_accounts) if high_risk_url_accounts else asyncio.sleep(0, result=[])
        geo_task = get_geo_mismatch_details(geo_mismatch_accounts) if geo_mismatch_accounts else asyncio.sleep(0, result=[])
        ip_task = get_login_ip_risk_details(high_risk_ip_accounts) if high_risk_ip_accounts else asyncio.sleep(0, result=[])
        email_task = get_login_email_risk_details(high_risk_email_accounts) if high_risk_email_accounts else asyncio.sleep(0, result=[])
        peak_task = get_abnormal_peaks_details(abnormal_peak_accounts) if abnormal_peak_accounts else asyncio.sleep(0, result=[])

        step6_results = await asyncio.gather(
            asyncio.gather(*attr_tasks) if attr_tasks else asyncio.sleep(0, result=[]),
            url_task,
            geo_task,
            ip_task,
            email_task,
            peak_task,
        )

        attr_raw_list, url_results, geo_results, ip_results, email_results, peak_results = step6_results
        attr_results = {pid: res for pid, res in zip(attr_risk_accounts, attr_raw_list)} if attr_risk_accounts else {}

        # Step 6B1: Semrush intelligence on flagged domains
        flagged_domains = list(set([r["domain"] for r in url_results if r.get("flagged_actions", 0) > 0]))
        domain_intel = await get_domain_intelligence(flagged_domains) if flagged_domains else []
        domain_intel_by_domain = {d["url"]: d for d in domain_intel}

    # Step 7: Consolidation into nested JSON structure keyed by publisher_id
    with trace_span("pipeline.step_7_consolidation", {"publisher_id": publisher_id}):
        consolidated: Dict[str, Any] = {
            "primary_publisher_id": publisher_id,
            "jira_ticket_id": jira_ticket_id,
            "account_list": account_list,
            "publishers": {}
        }

        summary_by_pub = {r["publisher_id"]: r for r in summaries}
        top_programs_by_pub = {r["publisher_id"]: r.get("top_programs", "") for r in top_programs_raw}
        mkpl_by_pub: Dict[int, List[Dict[str, Any]]] = {}
        for m in mkpl_history:
            mkpl_by_pub.setdefault(m["publisher_id"], []).append(m)

        jira_by_pub: Dict[int, List[Dict[str, Any]]] = {}
        for j in jira_history:
            for pid_str in (j.get("partner_id") or "").split():
                try:
                    pid = int(pid_str)
                    jira_by_pub.setdefault(pid, []).append(j)
                except ValueError:
                    pass

        for pub_id in account_list:
            pub_url_details = [u for u in url_results if u["publisher_id"] == pub_id]
            for u in pub_url_details:
                if u["domain"] in domain_intel_by_domain:
                    u["traffic_intelligence"] = domain_intel_by_domain[u["domain"]]

            consolidated["publishers"][str(pub_id)] = {
                "summary": summary_by_pub.get(pub_id, {}),
                "top_programs": top_programs_by_pub.get(pub_id, ""),
                "marketplace_history": mkpl_by_pub.get(pub_id, []),
                "compliance_history": jira_by_pub.get(pub_id, []),
                "risk_analysis": {
                    "7D": risk_by_pub_7d.get(pub_id, {}),
                    "30D": risk_by_pub_30d.get(pub_id, {}),
                    "365D": risk_by_pub_365d.get(pub_id, {})
                },
                "detailed_risk": {
                    "attribution_risk": attr_results.get(pub_id, []),
                    "url_risk": pub_url_details,
                    "geo_mismatch": [g for g in geo_results if g["publisher_id"] == pub_id],
                    "login_ip_risk": [ip for ip in ip_results if ip["account_id"] == pub_id],
                    "login_email_risk": [em for em in email_results if em["account_id"] == pub_id],
                    "abnormal_peaks": [pk for pk in peak_results if pk["publisher_id"] == pub_id]
                }
            }

    return consolidated
