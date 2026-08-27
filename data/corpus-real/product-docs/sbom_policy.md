# Software Bill of Materials — Generation and Disclosure Policy

## Scope of this document

This document sets out how a software bill of materials is produced for the
cloud email encryption service, what it contains, how long it is kept, who can
obtain it, and how it is used to answer a component-level vulnerability
question quickly.

## Format and generation

A bill of materials is generated for every deployable artefact at build time in
CycloneDX 1.5, in JSON encoding. CycloneDX is used rather than SPDX because its
vulnerability and dependency-relationship extensions carry the information the
support workflow actually needs; an SPDX 2.3 rendering can be produced on
request for customers whose own tooling requires it.

Generation happens inside the build, from the resolved dependency graph, not
afterwards by scanning a finished image. Scanning a finished image finds what a
scanner can recognise; building from the resolved graph records what was
actually linked, including components a scanner would miss because they were
vendored or statically linked.

The document is generated for each artefact and for each container image, and
the two are related in the same document set so that a question about "the
gateway" can be answered without the asker needing to know how the gateway is
packaged.

## Contents

Each entry carries the component name, version, package URL, supplier where
known, declared licence, cryptographic hash of the retrieved artefact, and the
dependency relationship — whether the component is a direct dependency of the
application or is pulled in transitively, and by what.

Transitive depth is recorded rather than flattened. When a report names a
vulnerable component, the practical question is almost always whether the
service reaches the vulnerable code path, and knowing that the component is
four levels down behind a package that does not call the affected function is
most of the answer.

Build-time-only components are marked as such and separated from those present
at runtime, because a vulnerability in a build tool and a vulnerability in a
shipped library warrant different responses.

## Signing and retention

Each document is signed at generation with the same key used for artefact
signing, so a document obtained from any route can be verified as the one this
build produced. The document is stored alongside the artefact it describes and
retained for the supported life of the release plus twelve months.

Documents for every release still in support are retrievable at any time. This
matters when a report lands about a component that was removed two releases
ago: the question is whether any supported release still contains it, and that
question is answered from the stored set rather than from memory.

## Disclosure to customers

A customer under a mutual non-disclosure agreement may obtain the bill of
materials for any release it is running, on request through the account team,
in CycloneDX 1.5 or SPDX 2.3.

Disclosure requires the non-disclosure agreement. The complete component
inventory of a security product is a useful document to an attacker choosing
where to look, and the balance of interest favours giving it to customers who
have a relationship rather than publishing it. Customers who need it for their
own supply chain programme get it; the request is routine and is not treated as
an exception.

## Using it to answer a vulnerability question

When a widely-reported vulnerability lands, the sequence is: query the stored
documents across every supported release for the component and version range;
determine for each match whether the affected code path is reachable in the
service's configuration; assign severity and a remediation target from the
standing schedule; and communicate to customers who have asked or, where the
determination is that customers are affected, to all of them.

The first step takes minutes because the documents are stored and queryable
rather than being regenerated under pressure. The second step is the one that
takes judgement, and having the first step already done is what leaves time for
it.

## Upstream expectations

Suppliers of components used inside the trust boundary are expected to publish
their own bill of materials, or to provide one on request. Where a supplier
cannot, that is recorded as a supply chain risk against the component with an
owner, and it weighs against the component in the review that happens whenever
a replacement is considered.
