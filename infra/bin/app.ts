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

const env = {
  account: process.env.CDK_DEFAULT_ACCOUNT,
  region: process.env.CDK_DEFAULT_REGION ?? 'us-east-1',
};

const appName = 'rfp-copilot';
const stage = app.node.tryGetContext('stage') ?? 'dev';
const prefix = `${appName}-${stage}`;

// Stacks deploy in dependency order. Each stack exposes typed exports
// (bucket ARNs, table names) via public readonly fields —
// consuming stacks import constructs directly rather than via SSM,
// which keeps the dependency graph explicit in CDK.
const storage = new StorageStack(app, `${prefix}-storage`, { env });

const data = new DataStack(app, `${prefix}-data`, { env });

const knowledgeBase = new KnowledgeBaseStack(app, `${prefix}-knowledge-base`, {
  env,
  referenceCorpusBucket: storage.referenceCorpusBucket,
  referenceKey: storage.referenceKey,
});

const orchestration = new OrchestrationStack(app, `${prefix}-orchestration`, {
  env,
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

new StaticSiteStack(app, `${prefix}-static-site`, {
  env: { account: env.account, region: 'us-east-1' }, // CloudFront ACM must be us-east-1
  domainName: 'rfp-copilot.meringue-app.com',
});

cdk.Tags.of(app).add('project', appName);
cdk.Tags.of(app).add('stage', stage);
cdk.Tags.of(app).add('managed-by', 'cdk');
