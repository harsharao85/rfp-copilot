# Architecture Diagrams

Mermaid diagrams for the RFP Redlining Copilot. These render natively
in GitHub, VS Code, and most Markdown viewers. Paste into slides via
mermaid.live → export PNG/SVG.

See `architecture-plan.md` §3–§5 for the text description that these
diagrams visualize.

---

## 1. End-to-end data flow

```mermaid
flowchart TB
    User([Sales Rep / SME])
    CF[CloudFront + Amplify UI]
    APIGW[API Gateway<br/>REST + WebSocket]
    Cognito[Cognito / IAM Identity Center]
    Cedar[Verified Permissions<br/>Cedar policies]

    S3In[(S3 Incoming<br/>KMS-CMK + Object Lock)]
    EB{EventBridge}
    SFN[Step Functions Standard<br/>state machine]

    Parser[λ Excel Parser<br/>openpyxl]
    DMap[[Distributed Map<br/>concurrency ≤ 25]]
    Retriever[λ Retriever]
    Generator[λ Generator<br/>Claude Sonnet 4.6]
    Scorer[λ Confidence Scorer]
    Rules[λ Hard Rules Engine]
    Writer[λ Excel Writer]

    Kendra[(Amazon Kendra<br/>unified index)]
    Neptune[(Neptune Serverless<br/>relationship graph)]
    Bedrock[Bedrock + Guardrails]
    LRS[(DynamoDB<br/>jobs, questions, reviews)]

    S3Out[(S3 Output<br/>answered workbooks)]
    SES[SES / Slack notify]
    ReviewUI[SME Review UI<br/>React + WebSocket]
    QB[Q Business<br/>interactive assistant]

    User -->|upload RFP| CF --> APIGW
    APIGW -.authn/authz.-> Cognito
    APIGW -.policy.-> Cedar
    APIGW --> S3In --> EB --> SFN

    SFN --> Parser --> DMap
    DMap --> Retriever --> Generator --> Scorer --> Rules
    Retriever -.query.-> Kendra
    Retriever -.graph query.-> Neptune
    Generator -.invoke.-> Bedrock
    DMap --> Writer
    Writer --> S3Out --> SES --> User

    Parser -.persist.-> LRS
    Rules -.persist.-> LRS

    SFN -.waitForTaskToken<br/>amber/red only.-> ReviewUI
    ReviewUI -.SendTaskSuccess.-> SFN
    ReviewUI -.approved edits<br/>(flywheel).-> Kendra
    ReviewUI -.approved edits<br/>(flywheel).-> Neptune

    User -.interactive triage.-> QB -.same index.-> Kendra

    classDef storage fill:#ffe4b5,stroke:#8b4513,color:#000
    classDef compute fill:#b5d8ff,stroke:#1e5f8b,color:#000
    classDef ai fill:#d5ffb5,stroke:#2e7d32,color:#000
    classDef security fill:#ffb5b5,stroke:#b71c1c,color:#000
    classDef ui fill:#e1bee7,stroke:#4a148c,color:#000

    class S3In,S3Out,Kendra,Neptune,LRS storage
    class Parser,Retriever,Generator,Scorer,Rules,Writer,SFN,DMap,EB compute
    class Bedrock ai
    class Cognito,Cedar security
    class User,CF,APIGW,SES,ReviewUI,QB ui
```

---

## 2. Per-question pipeline (inside the Distributed Map)

```mermaid
flowchart LR
    Q[Question] --> R[Retrieve<br/>Kendra + Neptune]
    R --> G[Generate<br/>Sonnet 4.6 + Guardrails]
    G --> S[Score<br/>composite H·R·C·F·G]
    S --> HR{Hard Rules}
    HR -->|pricing| RED1[RED]
    HR -->|compliance| AMB1[AMBER]
    HR -->|unapproved ref| AMB2[AMBER]
    HR -->|forward-looking| AMB3[AMBER]
    HR -->|clean| KEEP[Keep tier]

    KEEP --> T{Tier?}
    RED1 --> T
    AMB1 --> T
    AMB2 --> T
    AMB3 --> T

    T -->|≥ 0.80| Green[🟢 Green<br/>Rep review]
    T -->|0.55–0.80| Amber[🟡 Amber<br/>SME review required]
    T -->|< 0.55| Red[🔴 Red<br/>SME + Commercial/Compliance]

    classDef hard fill:#ffb5b5,stroke:#b71c1c,color:#000
    class HR,RED1,AMB1,AMB2,AMB3 hard
```

---

## 3. Confidence composite formula (visualization)

```mermaid
flowchart LR
    H["H · 0.45<br/>Prior-answer<br/>similarity"] --> SUM[Σ]
    R["R · 0.25<br/>Retrieval<br/>strength"] --> SUM
    C["C · 0.15<br/>Source<br/>coverage"] --> SUM
    F["F · 0.10<br/>Freshness<br/>decay"] --> SUM
    G["G · 0.05<br/>Guardrail<br/>clean"] --> SUM
    SUM --> Cap{H = 0?}
    Cap -->|yes| Amber[Cap at Amber]
    Cap -->|no| Score[Composite Score]
    Score --> Rules[Hard-rule overrides]
    Rules --> Final[Final Tier]
```

---

## 4. Deployment topology (AWS account view)

```mermaid
flowchart TB
    subgraph AWS["AWS Account — single region"]
        subgraph Public["Public surfaces"]
            CF[CloudFront]
            APIGW[API Gateway]
        end

        subgraph VPC["VPC (private isolated subnets, no NAT)"]
            subgraph Compute["Compute tier"]
                Lambdas[7 Lambdas<br/>Python 3.12]
                SFN[Step Functions<br/>Standard state machine]
            end

            subgraph Endpoints["PrivateLink endpoints"]
                BedrockEP[Bedrock Runtime]
                KendraEP[Kendra]
                S3EP[S3 Gateway]
                DDBEP[DynamoDB Gateway]
                KMSEP[KMS]
                SMEP[Secrets Manager]
            end

            Lambdas -.-> BedrockEP
            Lambdas -.-> KendraEP
            Lambdas -.-> S3EP
            Lambdas -.-> DDBEP
            SFN -.-> Lambdas
        end

        subgraph Managed["Managed services"]
            Bedrock[Bedrock<br/>Sonnet 4.6 + Haiku 4.5]
            Guardrails[Guardrails]
            Kendra[Kendra Index]
            Neptune[Neptune Serverless]
            S3[S3 Buckets × 4<br/>CMK-encrypted]
            DDB[DynamoDB × 4]
            QB[Q Business App]
        end

        subgraph Observability
            CW[CloudWatch]
            XRay[X-Ray]
            CT[CloudTrail]
            GD[GuardDuty]
            Macie[Macie]
        end

        CF --> APIGW
        APIGW --> Lambdas

        BedrockEP --> Bedrock
        Bedrock --> Guardrails
        KendraEP --> Kendra
        Neptune --- VPC
        QB --> Kendra

        Lambdas -.logs/traces.-> CW
        Lambdas -.traces.-> XRay
        Bedrock -.audit.-> CT
    end

    classDef cmk fill:#ffe4b5,stroke:#8b4513,color:#000
    classDef vpc fill:#b5d8ff,stroke:#1e5f8b,color:#000
    class S3,DDB,Neptune cmk
```

---

## How to use these for the demo deck

1. Open <https://mermaid.live> in a browser.
2. Paste any block (without the backticks) into the editor.
3. Export PNG (2x scale) or SVG.
4. Drop into slides; each diagram is self-contained.

The per-question pipeline (Diagram 2) is the single most effective
slide for explaining the confidence + hard-rules story to a mixed
technical/business audience. Lead with it.
