import * as cdk from 'aws-cdk-lib';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as kms from 'aws-cdk-lib/aws-kms';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as bedrock from 'aws-cdk-lib/aws-bedrock';
import * as s3vectors from 'aws-cdk-lib/aws-s3vectors';
import { Construct } from 'constructs';

export interface KnowledgeBaseStackProps extends cdk.StackProps {
  readonly referenceCorpusBucket: s3.IBucket;
  readonly referenceKey: kms.IKey;
}

/**
 * Bedrock Knowledge Base backed by S3 Vectors, indexing the reference
 * corpus bucket. Titan Embed v2 (1024 dims, cosine). Default fixed-size
 * chunking at 300 tokens / 20% overlap — adequate for compliance PDFs
 * and product-doc markdown at demo scale.
 *
 * Cost: ~$5/month at demo scale; OpenSearch Serverless is the documented
 * scale-up when sustained QPS or corpus > ~100k docs.
 *
 * Sidecar metadata (document_id, source_type, updated_at/approved_at,
 * topic_ids) is filterable by default — S3 Vectors treats every key as
 * filterable unless explicitly marked non-filterable, which is the
 * inverse of what some docs imply.
 */
export class KnowledgeBaseStack extends cdk.Stack {
  public readonly knowledgeBaseId: string;
  public readonly knowledgeBaseArn: string;
  public readonly dataSourceId: string;

  constructor(scope: Construct, id: string, props: KnowledgeBaseStackProps) {
    super(scope, id, props);

    // S3 Vectors bucket + index. Default SSE-S3 encryption — the vector
    // index stores embeddings only, not the source documents themselves.
    const vectorBucket = new s3vectors.CfnVectorBucket(this, 'VectorBucket', {});

    const vectorIndex = new s3vectors.CfnIndex(this, 'VectorIndex', {
      // No indexName — CFN generates a unique physical id so replacements
      // (e.g. if the metadata config below changes) don't collide on name.
      vectorBucketArn: vectorBucket.attrVectorBucketArn,
      dataType: 'float32',
      dimension: 1024, // Titan Embed v2
      distanceMetric: 'cosine',
      // S3 Vectors caps filterable metadata at 2048 bytes per vector.
      // Bedrock KB stores the chunk text under AMAZON_BEDROCK_TEXT and
      // chunk metadata under AMAZON_BEDROCK_METADATA — both blow that
      // budget on normal-sized chunks. Mark them non-filterable so they
      // don't count toward the limit (they stay retrievable).
      // Our sidecar keys (source_type, topic_ids, updated_at, approved_at,
      // document_id) remain filterable by default — none come close to 2 KB.
      metadataConfiguration: {
        nonFilterableMetadataKeys: ['AMAZON_BEDROCK_TEXT', 'AMAZON_BEDROCK_METADATA'],
      },
    });
    vectorIndex.addDependency(vectorBucket);

    // Bedrock service role: read corpus bucket, decrypt with its CMK,
    // invoke the embedding model, write/read the vector index.
    const embedModelArn = `arn:${cdk.Aws.PARTITION}:bedrock:${cdk.Aws.REGION}::foundation-model/amazon.titan-embed-text-v2:0`;

    const kbRole = new iam.Role(this, 'KnowledgeBaseRole', {
      assumedBy: new iam.ServicePrincipal('bedrock.amazonaws.com'),
      description: 'Bedrock Knowledge Base ingestion + retrieval role',
    });
    props.referenceCorpusBucket.grantRead(kbRole);
    // Required because the corpus bucket is KMS-CMK encrypted. Without
    // this, ingestion silently completes with zero documents scanned.
    props.referenceKey.grantDecrypt(kbRole);
    kbRole.addToPolicy(new iam.PolicyStatement({
      actions: ['bedrock:InvokeModel'],
      resources: [embedModelArn],
    }));
    kbRole.addToPolicy(new iam.PolicyStatement({
      actions: [
        's3vectors:PutVectors',
        's3vectors:GetVectors',
        's3vectors:QueryVectors',
        's3vectors:DeleteVectors',
        's3vectors:ListVectors',
        's3vectors:GetIndex',
      ],
      resources: [
        vectorBucket.attrVectorBucketArn,
        vectorIndex.attrIndexArn,
      ],
    }));

    // Name is account-scoped-unique AND required by the CFN schema. When a
    // property change forces replacement, CFN must be able to create the new
    // KB before deleting the old, so the new name must differ from whatever
    // existed. Bump the version suffix when an immutable property changes.
    const kb = new bedrock.CfnKnowledgeBase(this, 'KnowledgeBase', {
      name: 'rfp-copilot-corpus-kb-v2',
      description: 'RFP reference corpus — compliance, product docs, priors',
      roleArn: kbRole.roleArn,
      knowledgeBaseConfiguration: {
        type: 'VECTOR',
        vectorKnowledgeBaseConfiguration: {
          embeddingModelArn: embedModelArn,
        },
      },
      storageConfiguration: {
        type: 'S3_VECTORS',
        s3VectorsConfiguration: {
          indexArn: vectorIndex.attrIndexArn,
        },
      },
    });
    kb.addDependency(vectorIndex);
    kb.node.addDependency(kbRole);

    // Data source points at the bucket root — the sidecars' source_type
    // metadata discriminates compliance / product_doc / prior_rfp at query
    // time, so we don't need separate data sources per prefix.
    const dataSource = new bedrock.CfnDataSource(this, 'DataSource', {
      name: 'rfp-corpus-data-source',
      knowledgeBaseId: kb.attrKnowledgeBaseId,
      dataSourceConfiguration: {
        type: 'S3',
        s3Configuration: {
          bucketArn: props.referenceCorpusBucket.bucketArn,
        },
      },
      vectorIngestionConfiguration: {
        chunkingConfiguration: {
          chunkingStrategy: 'FIXED_SIZE',
          fixedSizeChunkingConfiguration: {
            maxTokens: 300,
            overlapPercentage: 20,
          },
        },
      },
    });
    dataSource.addDependency(kb);

    this.knowledgeBaseId = kb.attrKnowledgeBaseId;
    this.knowledgeBaseArn = kb.attrKnowledgeBaseArn;
    this.dataSourceId = dataSource.attrDataSourceId;

    new cdk.CfnOutput(this, 'KnowledgeBaseId',  { value: this.knowledgeBaseId });
    new cdk.CfnOutput(this, 'KnowledgeBaseArn', { value: this.knowledgeBaseArn });
    new cdk.CfnOutput(this, 'DataSourceId',     { value: this.dataSourceId });
    new cdk.CfnOutput(this, 'VectorIndexArn',   { value: vectorIndex.attrIndexArn });
  }
}
