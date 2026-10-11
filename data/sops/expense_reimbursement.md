---
intent: expense_reimbursement
sop_id: FIN-001
title: Expense Claims and Reimbursement
risk: low
target_scoped: false
allowed_roles: [employee, manager, hr, finance, it_admin, support_agent]
approver_roles: []
sensitive_tools: []
requires: [amount]
actions:
  - tool: jira_create_ticket
    args:
      project: FIN
      issue_type: Expense
      priority: "{expense_priority}"
      summary: "Expense reimbursement of {amount} for {requester_name}"
      description: "{request_text}"
  - tool: slack_post_message
    args:
      channel: "#finance-expenses"
      text: "New expense claim {ticket_id} from {requester_name} for {amount}."
  - tool: slack_dm
    args:
      user_id: "{requester_id}"
      text: "Your expense claim {ticket_id} has been queued for Finance review. Reimbursement follows the next payment run."
---
# Expense Claims and Reimbursement

## When to use
Use this SOP when an employee wants to be reimbursed, repaid or paid back for money they spent: travel, hotel, taxi, client dinner, team lunch, conference fees, office supplies, airfare, per diem. Typical wording: "reimburse my expense", "expense claim", "expense report", "paid out of pocket", "need to be paid back", "pending reimbursement".

## Required information
The amount, the purpose and the receipts.

## Procedure
1. Create an Expense ticket in the FIN project. Claims above 1000 are High priority.
2. Post the claim to #finance-expenses.
3. Tell the employee that Finance will review it and pay in the next run.

## Escalation
Claims without receipts or above 5000 go to the Finance controller.
