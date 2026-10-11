---
intent: hardware_request
sop_id: IT-004
title: Hardware and Peripheral Requests
risk: low
target_scoped: false
allowed_roles: [employee, manager, hr, finance, it_admin, support_agent]
approver_roles: []
sensitive_tools: []
requires: []
actions:
  - tool: jira_create_ticket
    args:
      project: ITSD
      issue_type: Hardware
      priority: Low
      summary: "Hardware request: {device} for {requester_name}"
      description: "{request_text}"
  - tool: slack_dm
    args:
      user_id: "{requester_id}"
      text: "Your hardware request {ticket_id} ({device}) is with the IT desk. Standard peripherals ship within three working days."
---
# Hardware and Peripheral Requests

## When to use
Use this SOP when someone needs new or replacement equipment: a laptop, monitor, keyboard, mouse, headset, webcam, docking station or charger that is broken, faulty, damaged, missing or simply needed. Typical wording: "I need a new monitor", "order a keyboard", "replacement laptop", "my headset is broken", "hardware request", "extra screen for my desk".

## Required information
The device type and the reason (new, replacement or additional).

## Procedure
1. Check the asset register for spare stock.
2. Log a Hardware ticket for the IT desk.
3. Confirm the expected delivery time to the requester.

## Escalation
Devices above 1500 need manager approval through Finance.
