# Evaluation report

Planner `local` - 200 synthetic cases - failure rate 0.0 - seed 7

| Metric | All | Dev (80) | Test (120) |
|---|---|---|---|
| Intent-routing accuracy | 95.0% | 98.8% | 92.5% |
| End-to-end outcome accuracy | 98.0% | 100.0% | 96.7% |

* Completion rate on completable requests: **97.2%**
* Final statuses: {'escalated': 28, 'completed': 139, 'denied': 18, 'rejected': 15}  |  escalation rate 14.0%
* Failure types: {'missing_information': 9, 'permission_denied': 18, 'low_confidence': 17, 'approval_rejected': 15, 'tool_error': 2}  |  total retries 0
* Audit chain valid: True (1618 rows)

## Handling time (excludes human approval wait on both sides)

| | Median | p95 |
|---|---|---|
| Automated, measured compute only | 0.022s | 0.033s |
| Automated, simulated (compute + virtual API latency) | 0.92s | 1.83s |
| Manual, MODELLED (assumed step times in app/timing.py) | 280s | 470s |

Median reduction vs modelled manual baseline: **99.7%**

## Per-intent routing accuracy

| Intent | Accuracy |
|---|---|
| access_request | 93% |
| customer_account_update | 100% |
| data_export_request | 100% |
| employee_offboarding | 100% |
| employee_onboarding | 100% |
| expense_reimbursement | 100% |
| hardware_request | 87% |
| incident_report | 93% |
| out_of_scope | 70% |
| password_reset | 100% |
| refund_request | 100% |
| software_license | 100% |
| vpn_connectivity | 100% |

## Misrouted cases (10)

* `C001` [dev] expected **out_of_scope**, got **employee_onboarding** (conf 0.2083): hey, send a thank-you note to our vendor
* `C083` [test] expected **hardware_request**, got **refund_request** (conf 0.1775): Hello, Please order a mouse for me ASAP.
* `C127` [test] expected **out_of_scope**, got **customer_account_update** (conf 0.227): Quick one - update my emergency cotnact details
* `C136` [test] expected **out_of_scope**, got **refund_request** (conf 0.1768): Urgent: Can you order lunch for the team
* `C137` [test] expected **incident_report**, got **out_of_scope** (conf 0.1494): Quick one - Major outage: the custoemr portal is unreachalbe Thank you.
* `C140` [test] expected **out_of_scope**, got **customer_account_update** (conf 0.1576): How do I apply for parental leave?
* `C147` [test] expected **out_of_scope**, got **expense_reimbursement** (conf 0.1324): need a parking pass for the office garage
* `C157` [test] expected **out_of_scope**, got **expense_reimbursement** (conf 0.1307): hello, reserve conference room 4b for 3pm thanks!
* `C168` [test] expected **hardware_request**, got **out_of_scope** (conf 0.1062): please arrange a webcam for the new project room
* `C171` [test] expected **access_request**, got **out_of_scope** (conf 0.1846): Quick one - Abhishek Jain needs access to the CRM (editor access) starting this week

## Cases with wrong final outcome (4)

* `C083` [test] expected completed, got escalated (missing_information): Hello, Please order a mouse for me ASAP.
* `C137` [test] expected completed, got escalated (low_confidence): Quick one - Major outage: the custoemr portal is unreachalbe Thank you.
* `C168` [test] expected completed, got escalated (low_confidence): please arrange a webcam for the new project room
* `C171` [test] expected completed, got escalated (low_confidence): Quick one - Abhishek Jain needs access to the CRM (editor access) starting this week
