---
intent: password_reset
sop_id: IT-001
title: Account Lockout and Password Reset
risk: low
target_scoped: true
allowed_roles: [employee, manager, hr, finance, it_admin, support_agent]
approver_roles: []
sensitive_tools: []
requires: []
actions:
  - tool: jira_create_ticket
    args:
      project: ITSD
      issue_type: Service Request
      priority: Low
      summary: "Password reset for {target_user_name}"
      description: "{request_text}"
  - tool: crm_reset_user_password
    args:
      user_id: "{target_user_id}"
  - tool: slack_dm
    args:
      user_id: "{requester_id}"
      text: "Ticket {ticket_id} logged. A temporary credential has been issued for {target_user_name}; they must change it at next sign-in and re-enrol MFA (SOP IT-001)."
  - tool: jira_update_ticket
    args:
      ticket_id: "{ticket_id}"
      status: Resolved
      comment: "Password reset completed and requester notified via Slack."
---
# Account Lockout and Password Reset

## When to use
Use this SOP when someone is locked out of their account, has forgotten their password or login credentials, cannot sign in, has an expired password, or the account was locked after too many failed login attempts. Typical wording: "I forgot my password", "locked out", "can't log in", "need a reset", "unlock my account", "credentials not working".

## Required information
The affected user (defaults to the requester). Managers may request a reset for their direct reports; HR and IT admins may request for anyone.

## Procedure
1. Verify the requester's identity from the directory record.
2. Confirm the affected user is active.
3. Issue a temporary password through the identity console and force a change at next sign-in.
4. Remind the user to re-enrol multi-factor authentication.
5. Close the ticket as Resolved once the user confirms sign-in.

## Escalation
If the account is flagged as compromised, escalate to Security Operations instead of resetting.
