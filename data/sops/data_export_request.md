---
intent: data_export_request
sop_id: PRIV-001
title: Customer Data Export (Data Subject Access Request)
risk: sensitive
target_scoped: false
allowed_roles: [support_agent, manager, finance]
approver_roles: [it_admin, manager]
sensitive_tools: [crm_export_customer_data]
requires: [customer_id]
actions:
  - tool: jira_create_ticket
    args:
      project: PRIV
      issue_type: DSAR
      priority: High
      summary: "Data subject access request for {customer_id}"
      description: "{request_text}"
  - tool: crm_export_customer_data
    args:
      customer_id: "{customer_id}"
  - tool: slack_post_message
    args:
      channel: "#privacy"
      text: "Data export {export_id} generated for {customer_id} under {ticket_id}."
  - tool: slack_dm
    args:
      user_id: "{requester_id}"
      text: "Export {export_id} for {customer_id} is ready ({ticket_id}). Share it only through the secure portal."
---
# Customer Data Export (Data Subject Access Request)

## When to use
Use this SOP when a customer exercises a privacy right and asks for a copy of the personal data held about them: data subject access request (DSAR), GDPR request, right of access, "export all their data", "everything you store about me", "copy of personal information", compliance data export.

## Required information
The customer id. The statutory deadline is 30 days.

## Procedure
1. Log a High-priority DSAR ticket in the PRIV project.
2. Obtain approval from an IT admin or a manager (never the requester).
3. Generate the export from the CRM.
4. Post to #privacy and notify the requester. Deliver only through the secure portal.

## Escalation
Requests that mention legal action are referred to the Data Protection Officer.
