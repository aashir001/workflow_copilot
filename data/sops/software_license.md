---
intent: software_license
sop_id: IT-003
title: Software License and Subscription Requests
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
      issue_type: Software
      priority: Low
      summary: "Software license: {software} for {requester_name}"
      description: "{request_text}"
  - tool: slack_dm
    args:
      user_id: "{requester_id}"
      text: "Your request {ticket_id} for a {software} license is with IT procurement. Licenses are usually assigned within two working days."
---
# Software License and Subscription Requests

## When to use
Use this SOP when someone needs a software license, licence, seat, subscription, renewal or installation of a commercial application such as Tableau, Alteryx, Figma, Adobe, Visio, Power BI, Zoom or an IDE. Typical wording: "I need a license for", "assign me a seat", "software request", "subscription", "licence renewal", "trial expired".

## Required information
The software name, the business reason and the duration.

## Procedure
1. Check whether an existing company license can be reassigned.
2. Log a Software ticket for IT procurement.
3. Tell the requester the expected turnaround.

## Escalation
Purchases above the annual software budget threshold need Finance approval.
