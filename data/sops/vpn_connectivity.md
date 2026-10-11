---
intent: vpn_connectivity
sop_id: NET-001
title: VPN and Remote Connectivity Troubleshooting
risk: low
target_scoped: false
allowed_roles: [employee, manager, hr, finance, it_admin, support_agent]
approver_roles: []
sensitive_tools: []
requires: []
actions:
  - tool: slack_dm
    args:
      user_id: "{requester_id}"
      text: "VPN troubleshooting (SOP NET-001): {sop_steps}"
  - tool: jira_create_ticket
    args:
      project: NET
      issue_type: Support
      priority: Medium
      summary: "VPN connectivity issue for {requester_name}"
      description: "{request_text}"
---
# VPN and Remote Connectivity Troubleshooting

## When to use
Use this SOP when the VPN will not connect, keeps disconnecting, is slow, shows an authentication or tunnel error, or the person cannot reach internal sites or the corporate network from home or while travelling. Typical wording: "VPN keeps dropping", "can't connect to VPN", "GlobalProtect error", "remote connection problems", "internal sites unreachable".

## Required information
The error message, the operating system and the network (home, hotel, mobile hotspot).

## Procedure
1. Restart the VPN client and confirm the device clock is correct.
2. Switch networks, for example from Wi-Fi to a mobile hotspot, to rule out a router block.
3. Update the VPN client to the latest version and sign in again with MFA.
4. If it still fails, share the error text with the Network team through the ticket.

## Escalation
Reports from many users at once are an outage: use the incident process instead.
