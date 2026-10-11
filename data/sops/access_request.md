---
intent: access_request
sop_id: IT-002
title: System Access and Permission Requests
risk: sensitive
target_scoped: true
allowed_roles: [employee, manager, hr, finance, it_admin, support_agent]
approver_roles: [manager, it_admin]
sensitive_tools: [crm_grant_access]
requires: []
actions:
  - tool: jira_create_ticket
    args:
      project: ITSD
      issue_type: Access
      priority: Medium
      summary: "{role} access to {system} for {target_user_name}"
      description: "{request_text}"
  - tool: crm_grant_access
    args:
      user_id: "{target_user_id}"
      system: "{system}"
      role: "{role}"
  - tool: slack_post_message
    args:
      channel: "#it-access"
      text: "{role} access to {system} granted to {target_user_name} under {ticket_id}."
  - tool: slack_dm
    args:
      user_id: "{requester_id}"
      text: "Your access request {ticket_id} ({role} on {system}) has been completed."
---
# System Access and Permission Requests

## When to use
Use this SOP when someone asks for access, permissions, entitlements or a role on a business system such as the CRM, Jira, the data warehouse, a shared drive, GitHub or BI dashboards. Typical wording: "I need access to", "please grant me", "provision admin access", "give edit permissions", "read-only access", "access upgrade", "requesting entry to the system".

## Required information
The system, the role level (viewer, editor, admin) and the business justification. If no level is stated, grant the least-privileged role (viewer).

## Procedure
1. Log an Access ticket.
2. Check that the requester may request access for the named person (self, direct report, or HR / IT admin).
3. Obtain approval: the line manager of the person receiving access, or an IT admin.
4. After approval, grant the role in the access console.
5. Notify the #it-access channel and the requester.

## Escalation
Admin-level access to finance or customer data also needs a second review by Security.
