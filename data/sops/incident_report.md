---
intent: incident_report
sop_id: OPS-001
title: Incident Reporting and Triage
risk: low
target_scoped: false
allowed_roles: [employee, manager, hr, finance, it_admin, support_agent]
approver_roles: []
sensitive_tools: []
requires: []
actions:
  - tool: jira_create_ticket
    args:
      project: INC
      issue_type: Incident
      priority: "{priority}"
      summary: "{priority} incident reported by {requester_name}"
      description: "{request_text}"
  - tool: slack_post_message
    args:
      channel: "#incidents"
      text: "{priority} incident {ticket_id} reported by {requester_name}: {request_text}"
  - tool: slack_dm
    args:
      user_id: "{requester_id}"
      text: "Incident {ticket_id} ({priority}) has been opened and the on-call engineer has been alerted."
---
# Incident Reporting and Triage

## When to use
Use this SOP when a production system, service, site, API or application is down, failing, unavailable, degraded or timing out, or when there is a security event such as suspicious logins, a breach, malware or ransomware, or when a data pipeline fails and dashboards show stale numbers. Typical wording: "production is down", "major outage", "service degradation", "security incident", "customers are complaining the site is down", "incident".

## Required information
What is affected, since when, and who is impacted.

## Procedure
1. Open an Incident ticket. Outages and security events are P1, degradations and failures are P2, everything else P3.
2. Alert the #incidents channel so the on-call engineer sees it.
3. Acknowledge the reporter.

## Escalation
P1 incidents page the on-call engineer and the incident commander.
