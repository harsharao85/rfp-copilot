import * as cdk from 'aws-cdk-lib';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as kms from 'aws-cdk-lib/aws-kms';
import { Construct } from 'constructs';

/**
 * Storage: four buckets, each with its own CMK.
 * - incoming      : uploaded RFPs (NDA-protected, Object Lock on)
 * - referenceCorpus: historical RFPs, whitepapers, Seismic/Gong exports (direct S3 access)
 * - output        : generated answer workbooks
 * - audit         : Bedrock invocation logs + per-job audit trail
 *
 * Per v0.4 plan §7: incoming RFPs are never auto-ingested into
 * referenceCorpus; promotion requires a deliberate post-deal-close step.
 */
export class StorageStack extends cdk.Stack {
  public readonly incomingBucket: s3.Bucket;
  public readonly referenceCorpusBucket: s3.Bucket;
  public readonly outputBucket: s3.Bucket;
  public readonly auditBucket: s3.Bucket;
  // Exposed so KnowledgeBaseStack can grant its service role kms:Decrypt
  // on the corpus bucket's CMK — required for KB ingestion to succeed.
  public readonly referenceKey: kms.IKey;

  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    const incomingKey = new kms.Key(this, 'IncomingKey', {
      enableKeyRotation: true,
      description: 'CMK for incoming RFP uploads (NDA-protected)',
    });

    const referenceKey = new kms.Key(this, 'ReferenceKey', {
      enableKeyRotation: true,
      description: 'CMK for reference corpus (historical RFPs, whitepapers)',
    });
    this.referenceKey = referenceKey;

    const outputKey = new kms.Key(this, 'OutputKey', {
      enableKeyRotation: true,
      description: 'CMK for generated answer workbooks',
    });

    const auditKey = new kms.Key(this, 'AuditKey', {
      enableKeyRotation: true,
      description: 'CMK for audit trail and Bedrock invocation logs',
    });

    this.incomingBucket = new s3.Bucket(this, 'IncomingBucket', {
      encryption: s3.BucketEncryption.KMS,
      encryptionKey: incomingKey,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      versioned: true,
      cors: [
        {
          allowedMethods: [s3.HttpMethods.PUT],
          allowedOrigins: ['https://rfp-copilot.meringue-app.com'],
          allowedHeaders: ['*'],
          maxAge: 3000,
        },
      ],
      lifecycleRules: [
        {
          id: 'archive-old-rfps',
          transitions: [
            {
              storageClass: s3.StorageClass.INFREQUENT_ACCESS,
              transitionAfter: cdk.Duration.days(30),
            },
            {
              storageClass: s3.StorageClass.GLACIER,
              transitionAfter: cdk.Duration.days(180),
            },
          ],
        },
      ],
    });

    this.referenceCorpusBucket = new s3.Bucket(this, 'ReferenceCorpusBucket', {
      encryption: s3.BucketEncryption.KMS,
      encryptionKey: referenceKey,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      versioned: true,
      // Publishes PutObject events to EventBridge. Downstream consumer
      // (orchestration-stack's ingestion_trigger Lambda) subscribes via an
      // EventBridge rule — no cross-stack dependency cycle that direct Lambda
      // notifications would create.
      eventBridgeEnabled: true,
    });

    this.outputBucket = new s3.Bucket(this, 'OutputBucket', {
      encryption: s3.BucketEncryption.KMS,
      encryptionKey: outputKey,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      versioned: true,
      lifecycleRules: [
        { id: 'expire-drafts', expiration: cdk.Duration.days(90) },
      ],
    });

    this.auditBucket = new s3.Bucket(this, 'AuditBucket', {
      encryption: s3.BucketEncryption.KMS,
      encryptionKey: auditKey,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      versioned: true,
      objectLockEnabled: true,
      objectLockDefaultRetention: s3.ObjectLockRetention.compliance(cdk.Duration.days(365 * 7)),
    });

    new cdk.CfnOutput(this, 'IncomingBucketName', { value: this.incomingBucket.bucketName });
    new cdk.CfnOutput(this, 'ReferenceCorpusBucketName', { value: this.referenceCorpusBucket.bucketName });
    new cdk.CfnOutput(this, 'OutputBucketName', { value: this.outputBucket.bucketName });
    new cdk.CfnOutput(this, 'AuditBucketName', { value: this.auditBucket.bucketName });
  }
}
