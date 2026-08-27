# AWS icon manifest — ENG-2 target answer path

Twenty-three icon slots in `echoworx-target-architecture-aws.excalidraw`. Each is a dashed
grey 44x44 square (34x34 for the Step Functions container). Open the file in Excalidraw,
drag the named icon from your AWS library onto the slot, then delete the slot rectangle.

Verified by `solutions-architect`, 27 Aug 2026, against the Q3 2026 asset package
(release `07312026`; AWS ships these quarterly, end of Jan / Apr / Jul).

| slot id | AWS service (exact product name) | icon asset |
|---|---|---|
| `sfn_slot` | AWS Step Functions | `Arch_AWS-Step-Functions_48` |
| `ui_slot` | Amazon CloudFront | `Arch_Amazon-CloudFront_48` |
| `cognito_slot` | Amazon Cognito | `Arch_Amazon-Cognito_48` |
| `api_slot` | Amazon API Gateway | `Arch_Amazon-API-Gateway_48` |
| `parser_slot` | AWS Lambda | `Arch_AWS-Lambda_48` |
| `diverg_slot` | AWS Lambda | `Arch_AWS-Lambda_48` |
| `classif_slot` | AWS Lambda | `Arch_AWS-Lambda_48` |
| `retriever_slot` | AWS Lambda | `Arch_AWS-Lambda_48` |
| `generator_slot` | AWS Lambda | `Arch_AWS-Lambda_48` |
| `scorer_slot` | AWS Lambda | `Arch_AWS-Lambda_48` |
| `hardrules_slot` | AWS Lambda | `Arch_AWS-Lambda_48` |
| `wdraft_slot` | AWS Lambda | `Arch_AWS-Lambda_48` |
| `batch_slot` | AWS Lambda | `Arch_AWS-Lambda_48` |
| `rgate_slot` | AWS Lambda | `Arch_AWS-Lambda_48` |
| `wfinal_slot` | AWS Lambda | `Arch_AWS-Lambda_48` |
| `harness_slot` | Amazon Bedrock AgentCore | `Arch_Amazon-Bedrock-AgentCore_48` |
| `gateway_slot` | Amazon Bedrock AgentCore | `Arch_Amazon-Bedrock-AgentCore_48` |
| `kb_slot` | Amazon Bedrock (Knowledge Bases) | `Arch_Amazon-Bedrock_48` |
| `corpus_slot` | Amazon S3 | `Arch_Amazon-Simple-Storage-Service_48` |
| `holdout_slot` | Amazon S3 | `Arch_Amazon-Simple-Storage-Service_48` |
| `priors_slot` | Amazon DynamoDB | `Arch_Amazon-DynamoDB_48` |
| `divcache_slot` | Amazon DynamoDB | `Arch_Amazon-DynamoDB_48` |
| `fork_slot` | **none** — Step Functions Choice state, not a resource | no icon; stays a diamond |

## Naming traps, confirmed

**There is no AWS product called "Amazon Bedrock AgentCore Gateway."** The service is
**Amazon Bedrock AgentCore**; Gateway, Harness, Runtime, Identity, Memory, Policy and
Evaluations are *components* of it. The longer string appears only in blog post titles.
The boxes are labelled `Amazon Bedrock` / `AgentCore Gateway` accordingly.

AgentCore has its own service icon under `Architecture-Service-Icons/Arch_Artificial-Intelligence` —
do **not** substitute the generic Amazon Bedrock icon for the Harness or Gateway boxes.

## Deliberately not verified, so not asserted

- **No category colour fill.** AWS does not publish a per-category hex palette; the colours
  exist only inside the deck's own assets. Boxes are therefore neutral white and let the
  icons carry their own colour, which is what most AWS-published reference architectures
  look like anyway. If you want category tinting, sample the fill from each category's
  `Arch_*_48.svg` in your local package rather than guessing.
- **No `Res_` resource icons.** Every asset above is an `Arch_` service icon. The resource-icon
  filenames could not be confirmed against a first-party source.
- **No Amazon S3 Vectors icon and no Knowledge Base resource icon** appear in the package,
  so `kb_slot` uses the Amazon Bedrock service icon and carries S3 Vectors in its label.
- **`Arch_Amazon-API-Gateway_48`'s folder** — placed under Application Integration here, but
  AWS has moved API Gateway between that and Networking & Content Delivery across product
  pages. Check the folder the asset actually sits in.

Every asset name other than AgentCore's follows the documented `Arch_<Product-Name-Hyphenated>_48`
pattern rather than being individually verified. Worth five minutes against your local package.

## Boundary decisions

- **Amazon CloudFront sits outside the Region box.** It is a global/edge service; drawing it
  inside `us-east-1` is an error AWS reviewers notice.
- **No VPC boundary.** Per `docs/architecture.md`, VPC + PrivateLink is an explicit PoC-scope
  omission and the Lambdas run outside a VPC. Drawing one would be a straightforward falsehood,
  and it is the kind a security reviewer at an email-encryption vendor will test.
- **Region is load-bearing, not incidental.** AgentCore Policy Guardrails is GA in five regions
  and `us-east-1` is the only plausible one here — SC4 is only buildable there.
