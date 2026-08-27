#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { StorageStack } from '../lib/storage-stack';
import { DataStack } from '../lib/data-stack';
import { KnowledgeBaseStack } from '../lib/knowledge-base-stack';
import { OrchestrationStack } from '../lib/orchestration-stack';
import { ObservabilityStack } from '../lib/observability-stack';
import { StaticSiteStack } from '../lib/static-site-stack';

const app = new cdk.App();

const appName = 'rfp-copilot';

// Everything environment-specific comes from CDK context or the operator's
// AWS session — nothing about a particular account, region or domain is
// baked into the repo (RISK-16).
//   npx cdk deploy --all -c stage=poc -c region=eu-west-1 \
//     -c corsOrigins=https://d111111abcdef8.cloudfront.net
const stage: string = app.node.tryGetContext('stage') ?? 'poc';
const region: string =
  app.node.tryGetContext('region') ?? process.env.CDK_DEFAULT_REGION ?? 'us-east-1';

const env = {
  account: process.env.CDK_DEFAULT_ACCOUNT,
  region,
};

/**
 * corsOrigins accepts a JSON array (`-c corsOrigins='["https://a","https://b"]'`)
 * or a comma-separated string. Defaults to `*`, which is fine for a throwaway
 * PoC front end but is announced loudly at synth time so it is never shipped
 * by accident.
 */
const corsContext: unknown = app.node.tryGetContext('corsOrigins');
let corsOrigins: string[];
let corsIsWildcardDefault = false;
if (Array.isArray(corsContext)) {
  corsOrigins = corsContext.map(String);
} else if (typeof corsContext === 'string' && corsContext.trim() !== '') {
  corsOrigins = corsContext.split(',').map((o) => o.trim()).filter((o) => o !== '');
} else {
  corsOrigins = ['*'];
  corsIsWildcardDefault = true;
}

const prefix = `${appName}-${stage}`;

// Stacks deploy in dependency order. Each stack exposes typed exports
// (bucket ARNs, table names) via public readonly fields —
// consuming stacks import constructs directly rather than via SSM,
// which keeps the dependency graph explicit in CDK.
const storage = new StorageStack(app, `${prefix}-storage`, { env, corsOrigins });

const data = new DataStack(app, `${prefix}-data`, { env });

const knowledgeBase = new KnowledgeBaseStack(app, `${prefix}-knowledge-base`, {
  env,
  referenceCorpusBucket: storage.referenceCorpusBucket,
  referenceKey: storage.referenceKey,
});

const orchestration = new OrchestrationStack(app, `${prefix}-orchestration`, {
  env,
  corsOrigins,
  incomingBucket: storage.incomingBucket,
  outputBucket: storage.outputBucket,
  referenceCorpusBucket: storage.referenceCorpusBucket,
  referenceKey: storage.referenceKey,
  jobsTable: data.jobsTable,
  questionsTable: data.questionsTable,
  reviewsTable: data.reviewsTable,
  customerRefsTable: data.customerRefsTable,
  knowledgeBaseId: knowledgeBase.knowledgeBaseId,
  knowledgeBaseArn: knowledgeBase.knowledgeBaseArn,
  dataSourceId: knowledgeBase.dataSourceId,
});

new ObservabilityStack(app, `${prefix}-observability`, {
  env,
  stateMachineArn: orchestration.stateMachineArn,
  jobsTableName: data.jobsTable.tableName,
});

// No custom domain, so no ACM certificate, so no us-east-1 pin: the site
// stack deploys into the same region as everything else.
const staticSite = new StaticSiteStack(app, `${prefix}-static-site`, { env });

if (corsIsWildcardDefault) {
  const message =
    'corsOrigins defaulted to ["*"] — every HTTP API and the incoming-bucket ' +
    'CORS policy will accept any origin. Pass -c corsOrigins=<https://your-site> ' +
    'before exposing this to anything real.';
  for (const stack of [storage, orchestration, staticSite]) {
    cdk.Annotations.of(stack).addWarning(message);
  }
}

cdk.Tags.of(app).add('project', appName);
cdk.Tags.of(app).add('stage', stage);
cdk.Tags.of(app).add('managed-by', 'cdk');
