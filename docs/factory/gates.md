# Gate contract

| Gate | Opens after | Human reviews | Evidence required in Notion | Agents blocked until Approved |
|---|---|---|---|---|
| G1 Scope | Brief Analyst done | Brief Summary, Success Criteria, Non-Goals, UoWs, Open Questions | All "must answer before G1" questions answered | solutions-architect, security-architect, finops-analyst |
| G2 Architecture | Architect + Security + FinOps done | ADRs (Proposed), diagrams, threat model, cost estimate | No Blocking Risk open | platform-engineer, builder |
| G3 Code Review | Per PR: Builder + QA + Security review | PR, Test Report, checkov output | QA Approve, no Critical/High security finding | merge (human merges) |
| G4 Deploy Approval | All UoWs merged, `cdk diff` posted | `cdk diff`, budget stack, runbook | Diff matches ADRs; budget in place | pipeline `cdk deploy` (GitHub Environment approval mirrors this gate) |
| G5 Presentation | Post-deploy e2e green | Deck outline (Canva), Exec Summary, Demo Script | All Success Criteria evidenced | nothing; human presents |

Rules: only a human sets `Verdict`. Agents may set `Pending` and attach evidence. A rejected gate
returns to the owning agent with the human's comment verbatim.
