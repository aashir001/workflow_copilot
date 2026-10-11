---
intent: refund_request
sop_id: CS-001
title: Customer Refund Processing
risk: sensitive
target_scoped: false
allowed_roles: [support_agent, finance, manager]
approver_roles: [finance, manager]
sensitive_tools: [crm_issue_refund]
requires: [order_id, amount]
actions:
  - tool: jira_create_ticket
    args:
      project: FIN
      issue_type: Refund
      priority: Medium
      summary: "Refund of {amount} on order {order_id}"
      description: "{request_text}"
  - tool: crm_issue_refund
    args:
      order_id: "{order_id}"
      amount: "{amount}"
      reason: "{request_text}"
  - tool: slack_post_message
    args:
      channel: "#finance-refunds"
      text: "Refund of {amount} issued on order {order_id} ({ticket_id})."
  - tool: slack_dm
    args:
      user_id: "{requester_id}"
      text: "Refund on {order_id} for {amount} has been processed ({ticket_id})."
---
# Customer Refund Processing

## When to use
Use this SOP when a customer wants a refund, their money back, a reversal of a duplicate or double charge, or compensation for an order that was damaged, never arrived, was cancelled or was not delivered. Typical wording: "refund order", "issue a refund", "customer wants money back", "charged twice", "duplicate charge", "full refund", "refund the customer".

## Required information
The order id. If no amount is stated, refund the full order value. The refund can never exceed the original order value.

## Procedure
1. Look up the order in the CRM and confirm it is eligible.
2. Log a Refund ticket.
3. Obtain approval from Finance or a manager (never the requester).
4. Issue the refund in the CRM.
5. Notify #finance-refunds and the requester.

## Escalation
Refunds on orders older than 90 days need Finance controller sign-off.
