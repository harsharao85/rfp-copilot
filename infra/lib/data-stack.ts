import * as cdk from 'aws-cdk-lib';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import { Construct } from 'constructs';

/**
 * DynamoDB tables for job state, per-question results, SME reviews,
 * and the closed-loop answer-library feedback.
 *
 * Partition-key design is intentionally single-table-friendly at
 * small scale but split across tables here for clarity of intent.
 * Refactor to single-table design when access patterns stabilize.
 */
export class DataStack extends cdk.Stack {
  public readonly jobsTable: dynamodb.Table;
  public readonly questionsTable: dynamodb.Table;
  public readonly reviewsTable: dynamodb.Table;
  public readonly customerRefsTable: dynamodb.Table;

  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    this.jobsTable = new dynamodb.Table(this, 'JobsTable', {
      partitionKey: { name: 'jobId', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      encryption: dynamodb.TableEncryption.AWS_MANAGED,
      pointInTimeRecovery: true,
      stream: dynamodb.StreamViewType.NEW_AND_OLD_IMAGES,
    });

    this.questionsTable = new dynamodb.Table(this, 'QuestionsTable', {
      partitionKey: { name: 'jobId', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'questionId', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      encryption: dynamodb.TableEncryption.AWS_MANAGED,
      pointInTimeRecovery: true,
    });

    // GSI for querying by confidence tier (green/amber/red) during review
    this.questionsTable.addGlobalSecondaryIndex({
      indexName: 'tier-index',
      partitionKey: { name: 'jobId', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'tier', type: dynamodb.AttributeType.STRING },
    });

    this.reviewsTable = new dynamodb.Table(this, 'ReviewsTable', {
      partitionKey: { name: 'jobId', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'reviewedAt', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      encryption: dynamodb.TableEncryption.AWS_MANAGED,
    });

    // LibraryFeedback removed (Phase F): SME-approved Q&A moved to the
    // Bedrock KB (source_type=sme_approved_answer) for unified semantic
    // retrieval. See docs/architecture.md §5 / §8.

    // Replaces Neptune Customer vertices. Retriever uses this for hard-rule #4
    // (customer-name gating). Production: add a GSI on public_reference to avoid
    // full-table scans.
    this.customerRefsTable = new dynamodb.Table(this, 'CustomerRefsTable', {
      partitionKey: { name: 'customerId', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      encryption: dynamodb.TableEncryption.AWS_MANAGED,
    });

    new cdk.CfnOutput(this, 'JobsTableName', { value: this.jobsTable.tableName });
    new cdk.CfnOutput(this, 'QuestionsTableName', { value: this.questionsTable.tableName });
    new cdk.CfnOutput(this, 'ReviewsTableName', { value: this.reviewsTable.tableName });
    new cdk.CfnOutput(this, 'CustomerRefsTableName', { value: this.customerRefsTable.tableName });
  }
}
