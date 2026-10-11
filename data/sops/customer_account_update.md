---
intent: customer_account_update
sop_id: CS-002
title: Customer Contact and Billing Detail Changes
risk: sensitive
target_scoped: false
allowed_roles: [support_agent, finance, manager]
approver_roles: [manager]
sensitive_tools: [crm_update_customer]
requires: [customer_id, field, value]
actions:
  - tool: jira_create_ticket
    args:
      project: CS
      issue_type: Account Change
      priority: Low
      summary: "Update {field} on customer {customer_id}"
      description: "{request_text}"
  - tool: crm_update_customer
    args:
      customer_id: "{customer_id}"
      field: "{field}"
      value: "{value}"
  - tool: slack_dm
    args:
      user_id: "{requester_id}"
      text: "Customer {customer_id}: {field} updated ({ticket_id})."
---
# Customer Contact and Billing Detail Changes

## When to use
Use this SOP when a customer's record in the CRM must be changed: new email address, new phone or mobile number, corrected billing address, or other contact details on an account. Typical wording: "update the email for customer", "change their phone number", "new billing address", "correct the contact details on file", "modify the account record".

## Required information
The customer id, which field to change (email, phone, billing_address) and the new value.

## Procedure
1. Log an Account Change ticket.
2. Obtain manager approval because contact and billing details are used for identity checks.
3. Apply the change in the CRM.
4. Confirm to the requester.

## Escalation
If the customer cannot be verified, do not change the record.
