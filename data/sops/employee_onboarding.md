---
intent: employee_onboarding
sop_id: HR-001
title: New Hire Onboarding and Provisioning
risk: low
target_scoped: true
allowed_roles: [manager, hr, it_admin]
approver_roles: []
sensitive_tools: []
requires: [target_user]
actions:
  - tool: jira_create_ticket
    args:
      project: HR
      issue_type: Onboarding
      priority: Medium
      summary: "Onboarding for new hire {target_user_name}"
      description: "{request_text}"
  - tool: crm_grant_access
    args:
      user_id: "{target_user_id}"
      system: crm
      role: standard
  - tool: slack_post_message
    args:
      channel: "#it-onboarding"
      text: "Onboarding {ticket_id} opened for {target_user_name}: accounts provisioned, workstation to be prepared before day one."
  - tool: slack_dm
    args:
      user_id: "{requester_id}"
      text: "Onboarding for {target_user_name} is in progress under {ticket_id}."
---
# New Hire Onboarding and Provisioning

## When to use
Use this SOP when a new hire, new joiner, new starter or new team member is about to start and needs accounts, email, tools, a workstation or standard access set up before day one. Typical wording: "new hire starts Monday", "onboard", "joining next week", "day one setup", "provision accounts for new employee", "kick off the onboarding checklist".

## Required information
The name of the new hire (they must already exist in the directory with status pending_start) and the start date.

## Procedure
1. Open an Onboarding ticket in the HR project.
2. Provision the standard role in the CRM and core systems.
3. Notify #it-onboarding so a workstation is prepared.
4. Tell the requesting manager that onboarding is in progress.

## Escalation
If the new hire's name is not given, ask the requester to resubmit with the name. Contractors follow the separate vendor process.
