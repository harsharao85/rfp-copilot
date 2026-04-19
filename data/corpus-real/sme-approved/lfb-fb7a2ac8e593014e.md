# SME-Approved Q&A — lfb-fb7a2ac8e593014e

**Approved:** sam.okafor@example.com on 2026-02-20  
**Topics:** data_residency, gdpr  
**Corroborated by:** dr_bcp_overview

## Question

Where is customer data stored? Is data residency configurable?

## Answer

Customer data is stored in the AWS region elected at tenant creation. Supported regions: us-east-1, us-west-2, eu-west-1, eu-central-1, ap-southeast-2. Data does not leave the elected region for operational purposes. Cross-region replication is available as an opt-in feature for disaster recovery. [dr_bcp_overview]
