---
intent: employee_offboarding
sop_id: HR-002
title: Employee Offboarding and Access Revocation
risk: sensitive
target_scoped: true
allowed_roles: [manager, hr, it_admin]
approver_roles: [hr, it_admin]
sensitive_tools: [crm_revoke_access]
requires: [target_user]
actions:
  - tool: jira_create_ticket
    args:
      project: HR
      issue_type: Offboarding
      priority: High
      summary: "Offboarding for {target_user_name}"
      description: "{request_text}"
  - tool: crm_revoke_access
    args:
      user_id: "{target_user_id}"
  - tool: slack_post_message
    args:
      channel: "#it-offboarding"
      text: "Offboarding {ticket_id}: all system access revoked for {target_user_name}. Equipment return pending."
  - tool: slack_dm
    args:
      user_id: "{requester_id}"
      text: "Offboarding for {target_user_name} is complete under {ticket_id}."
---
# Employee Offboarding and Access Revocation

## When to use
Use this SOP when an employee is leaving, resigning, being terminated, or has an exit or last working day, and their accounts and access must be removed. Typical wording: "is leaving the company", "last day is Friday", "revoke all access", "deactivate accounts", "leaver process", "employee exit", "terminate system access", "departing employee".

## Required information
The name of the departing employee and the last working day.

## Procedure
1. Open a High-priority Offboarding ticket.
2. Obtain approval from HR or an IT admin (never the requester).
3. Revoke all system access and deactivate the accounts.
4. Post confirmation in #it-offboarding and notify the requester.
5. Arrange collection of laptops and badges.

## Escalation
Involuntary exits are coordinated with HR and Security before any access change.
