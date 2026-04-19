import * as cdk from 'aws-cdk-lib';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as sfn from 'aws-cdk-lib/aws-stepfunctions';
import * as sfnTasks from 'aws-cdk-lib/aws-stepfunctions-tasks';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as bedrock from 'aws-cdk-lib/aws-bedrock';
import * as apigatewayv2 from 'aws-cdk-lib/aws-apigatewayv2';
import * as apigatewayv2Integrations from 'aws-cdk-lib/aws-apigatewayv2-integrations';
import * as events from 'aws-cdk-lib/aws-events';
import * as targets from 'aws-cdk-lib/aws-events-targets';
import * as python from '@aws-cdk/aws-lambda-python-alpha';
import * as path from 'path';
import * as fs from 'fs';
import * as crypto from 'crypto';
import { Construct } from 'constructs';

/** Hash an entire directory so shared/ changes bust the CDK asset cache. */
function hashDir(dir: string): string {
  const hash = crypto.createHash('sha256');
  const walk = (d: string) => {
    for (const entry of fs.readdirSync(d).sort()) {
      const full = path.join(d, entry);
      if (fs.statSync(full).isDirectory()) { walk(full); }
      else { hash.update(fs.readFileSync(full)); }
    }
  };
  walk(dir);
  return hash.digest('hex').slice(0, 16);
}

export interface OrchestrationStackProps extends cdk.StackProps {
  readonly incomingBucket: s3.IBucket;
  readonly outputBucket: s3.IBucket;
  readonly referenceCorpusBucket: s3.IBucket;
  readonly jobsTable: dynamodb.ITable;
  readonly questionsTable: dynamodb.ITable;
  readonly reviewsTable: dynamodb.ITable;
  readonly libraryFeedbackTable: dynamodb.ITable;
  readonly customerRefsTable: dynamodb.ITable;
}

/**
 * Durable orchestration envelope around the non-deterministic agent
 * reasoning. Step Functions Standard is chosen for unlimited
 * .waitForTaskToken (SME review) and full execution history (audit).
 *
 * Plain Map fans out per-question work at concurrency 10 — sufficient
 * for 30-question demos within Bedrock on-demand TPS quotas.
 * Scale-up path: Distributed Map + S3 itemReader (documented in architecture §11).
 *
 * Bedrock Guardrails are configured here; the generator Lambda attaches
 * them on every InvokeModel call via the guardrail ID env var.
 */
export class OrchestrationStack extends cdk.Stack {
  public readonly stateMachineArn: string;
  public readonly guardrailId: string;
  public readonly reviewApiUrl: string;

  constructor(scope: Construct, id: string, props: OrchestrationStackProps) {
    super(scope, id, props);

    // ----- Bedrock Guardrails -----
    // Denied-topics, PII filter, and tone-drift policy per
    // docs/architecture.md §7. Must be reviewed annually by
    // General Counsel; changes are a policy change, not a refactor.
    const guardrail = new bedrock.CfnGuardrail(this, 'AnswerGuardrail', {
      name: 'rfp-copilot-answer-guardrail',
      description: 'Denied topics + PII + tone drift for RFP generator',
      blockedInputMessaging: 'This input contains content that cannot be processed.',
      blockedOutputsMessaging: 'This response has been blocked by policy. An SME will review this question.',
      topicPolicyConfig: {
        topicsConfig: [
          {
            name: 'pricing_and_commercial',
            definition: 'Specific prices, discounts, volume tiers, subscription fees, or other commercial terms.',
            type: 'DENY',
            examples: [
              'Enterprise tier pricing is $50 per user per month.',
              'We offer a 15% discount at 5,000+ seats.',
            ],
          },
          {
            name: 'competitor_disparagement',
            definition: 'Negative statements about named competitors or comparative claims that denigrate competing products.',
            type: 'DENY',
          },
          {
            name: 'unqualified_compliance_claim',
            definition: 'Compliance or certification claims without an explicit source citation.',
            type: 'DENY',
          },
        ],
      },
      sensitiveInformationPolicyConfig: {
        piiEntitiesConfig: [
          { type: 'EMAIL', action: 'ANONYMIZE' },
          { type: 'PHONE', action: 'ANONYMIZE' },
          { type: 'US_SOCIAL_SECURITY_NUMBER', action: 'BLOCK' },
          { type: 'CREDIT_DEBIT_CARD_NUMBER', action: 'BLOCK' },
        ],
      },
      contentPolicyConfig: {
        filtersConfig: [
          { type: 'HATE', inputStrength: 'HIGH', outputStrength: 'HIGH' },
          { type: 'INSULTS', inputStrength: 'HIGH', outputStrength: 'HIGH' },
          { type: 'MISCONDUCT', inputStrength: 'HIGH', outputStrength: 'HIGH' },
          { type: 'PROMPT_ATTACK', inputStrength: 'HIGH', outputStrength: 'NONE' },
        ],
      },
    });
    this.guardrailId = guardrail.attrGuardrailId;

    // ----- Lambda factory with shared/ bundling -----
    const lambdasRoot = path.join(__dirname, '..', '..', 'lambdas');

    // SHARED_VERSION: bump this when lambdas/shared/ changes to force Lambda config updates,
    // since CDK asset hashing only covers the entry directory (not the volume-mounted shared/).
    const sharedEnv: Record<string, string> = {
      JOBS_TABLE: props.jobsTable.tableName,
      QUESTIONS_TABLE: props.questionsTable.tableName,
      LIBRARY_FEEDBACK_TABLE: props.libraryFeedbackTable.tableName,
      CUSTOMER_REFS_TABLE: props.customerRefsTable.tableName,
      REFERENCE_CORPUS_BUCKET: props.referenceCorpusBucket.bucketName,
      GUARDRAIL_ID: this.guardrailId,
      GUARDRAIL_VERSION: 'DRAFT',
      LOG_LEVEL: 'INFO',
      SHARED_VERSION: '11',
      OUTPUT_BUCKET: props.outputBucket.bucketName,
    };

    /**
     * PythonFunction installs requirements.txt automatically.
     * commandHooks.afterBundling copies the shared/ package into the
     * Lambda's bundle so handlers can `from shared.models import ...`.
     * Avoids a Lambda layer — simpler to reason about at this scale.
     */
    const makeLambda = (name: string, entryDir: string, timeout = 30): python.PythonFunction => {
      const entryPath = path.join(lambdasRoot, entryDir);
      const sharedPath = path.join(lambdasRoot, 'shared');
      // Include shared/ in the asset hash so changes to bedrock_client.py etc. bust the cache.
      const assetHash = hashDir(entryPath) + '-' + hashDir(sharedPath);
      return new python.PythonFunction(this, name, {
        entry: entryPath,
        runtime: lambda.Runtime.PYTHON_3_12,
        index: 'handler.py',
        handler: 'lambda_handler',
        memorySize: 1024,
        timeout: cdk.Duration.seconds(timeout),
        environment: sharedEnv,
        logRetention: logs.RetentionDays.ONE_MONTH,
        tracing: lambda.Tracing.ACTIVE,
        bundling: {
          assetHashType: cdk.AssetHashType.CUSTOM,
          assetHash,
          assetExcludes: ['tests', '__pycache__', '*.pyc', '.pytest_cache', '.ruff_cache', '.mypy_cache'],
          volumes: [
            { hostPath: sharedPath, containerPath: '/var/shared' },
          ],
          commandHooks: {
            beforeBundling: () => [],
            afterBundling: (_inputDir: string, outputDir: string) => [
              `cp -r /var/shared ${outputDir}/shared`,
            ],
          },
        },
      });
    };

    const parserFn = makeLambda('ParserFn', 'excel_parser', 60);
    const classifierFn = makeLambda('ClassifierFn', 'question_classifier');
    const retrieverFn = makeLambda('RetrieverFn', 'retriever', 60);
    const generatorFn = makeLambda('GeneratorFn', 'generator', 90);
    const scorerFn = makeLambda('ScorerFn', 'confidence_scorer');
    const rulesFn = makeLambda('RulesFn', 'hard_rules');
    const writerFn = makeLambda('WriterFn', 'excel_writer', 60);
    const reviewGateFn = makeLambda('ReviewGateFn', 'review_gate', 30);
    const reviewApiFn = makeLambda('ReviewApiFn', 'review_api', 30);
    // Phase C: mock sources + staleness daemon
    const mockSourcesFn = makeLambda('MockSourcesFn', 'mock_sources');
    const stalenessDaemonFn = makeLambda('StalenessDaemonFn', 'staleness_daemon', 60);
    // Task 1: upload API — presign, start, status, download
    const uploadApiFn = makeLambda('UploadApiFn', 'upload_api');

    // ----- Least-privilege grants -----
    props.incomingBucket.grantRead(parserFn);
    props.outputBucket.grantWrite(parserFn);  // parser writes question manifest to manifests/
    props.outputBucket.grantReadWrite(writerFn);
    props.incomingBucket.grantRead(writerFn);
    props.jobsTable.grantReadWriteData(parserFn);
    props.jobsTable.grantReadWriteData(writerFn);
    props.questionsTable.grantReadWriteData(parserFn);
    props.questionsTable.grantReadWriteData(generatorFn);
    props.questionsTable.grantReadWriteData(scorerFn);
    props.questionsTable.grantReadWriteData(rulesFn);
    props.questionsTable.grantReadData(writerFn);

    props.referenceCorpusBucket.grantRead(retrieverFn);
    props.customerRefsTable.grantReadData(retrieverFn);
    props.libraryFeedbackTable.grantReadData(retrieverFn);
    // UpdateItem only — retriever writes suppressed_prior_count to existing question items
    retrieverFn.addToRolePolicy(new iam.PolicyStatement({
      actions: ['dynamodb:UpdateItem'],
      resources: [props.questionsTable.tableArn],
    }));
    props.libraryFeedbackTable.grantReadWriteData(stalenessDaemonFn);

    for (const fn of [retrieverFn, generatorFn, classifierFn, scorerFn]) {
      fn.addToRolePolicy(new iam.PolicyStatement({
        actions: ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream'],
        resources: ['*'], // narrow to specific foundation-model ARNs in prod
      }));
    }
    // Guardrail invocation permission on the generator only
    generatorFn.addToRolePolicy(new iam.PolicyStatement({
      actions: ['bedrock:ApplyGuardrail'],
      resources: [guardrail.attrGuardrailArn],
    }));

    // ----- Per-question inner workflow (runs inside Map) -----
    // classify → retrieve → generate → score → applyRules
    // Classifier outputs topics + dispatch_plan into $.classification;
    // the retriever reads $.classification.dispatch_plan to route sources.
    const classify = new sfnTasks.LambdaInvoke(this, 'Classify', {
      lambdaFunction: classifierFn,
      payloadResponseOnly: true,
      resultPath: '$.classification',
    });

    const retrieve = new sfnTasks.LambdaInvoke(this, 'Retrieve', {
      lambdaFunction: retrieverFn,
      payloadResponseOnly: true,
      resultPath: '$.retrieval',
    });
    const generate = new sfnTasks.LambdaInvoke(this, 'Generate', {
      lambdaFunction: generatorFn,
      payloadResponseOnly: true,
      resultPath: '$.generation',
    });
    const score = new sfnTasks.LambdaInvoke(this, 'Score', {
      lambdaFunction: scorerFn,
      payloadResponseOnly: true,
      resultPath: '$.score',
    });
    const applyRules = new sfnTasks.LambdaInvoke(this, 'ApplyHardRules', {
      lambdaFunction: rulesFn,
      payloadResponseOnly: true,
      resultPath: '$.final',
    });

    // Per-question retries on Lambda/Bedrock throttles
    for (const task of [classify, retrieve, generate, score, applyRules]) {
      task.addRetry({
        errors: ['Lambda.TooManyRequestsException', 'Lambda.ServiceException', 'States.TaskFailed'],
        interval: cdk.Duration.seconds(2),
        backoffRate: 2,
        maxAttempts: 5,
      });
    }

    const perQuestion = sfn.Chain.start(classify).next(retrieve).next(generate).next(score).next(applyRules);

    // ----- Outer workflow: parse → Map → write -----
    const parse = new sfnTasks.LambdaInvoke(this, 'ParseWorkbook', {
      lambdaFunction: parserFn,
      payloadResponseOnly: true,
      resultPath: '$.parsed',
    });

    // Parser returns questions inline ($.parsed.questions) as well as writing
    // the S3 manifest. At 30-question scale the inline payload is ~15 KB —
    // well under the 256 KB Step Functions state limit. Scale-up path:
    // switch to Distributed Map + S3JsonItemReader (docs/architecture.md §11).
    const map = new sfn.Map(this, 'ProcessQuestions', {
      maxConcurrency: 10,
      itemsPath: sfn.JsonPath.stringAt('$.parsed.questions'),
      resultPath: sfn.JsonPath.DISCARD, // per-question results written to DynamoDB
    });
    map.itemProcessor(perQuestion);

    const write = new sfnTasks.LambdaInvoke(this, 'WriteOutputWorkbook', {
      lambdaFunction: writerFn,
      payloadResponseOnly: true,
      resultPath: '$.output',
      payload: sfn.TaskInput.fromObject({
        jobId: sfn.JsonPath.stringAt('$.jobId'),
        sourceBucket: sfn.JsonPath.stringAt('$.bucket'),
        sourceKey: sfn.JsonPath.stringAt('$.key'),
      }),
    });

    // SME review gate — pauses execution until the review UI calls SendTaskSuccess.
    // The review_gate Lambda stores the task token in jobsTable; the review_api
    // Lambda (behind the HTTP API) retrieves it and resumes the execution.
    const reviewGate = new sfnTasks.LambdaInvoke(this, 'WaitForReview', {
      lambdaFunction: reviewGateFn,
      integrationPattern: sfn.IntegrationPattern.WAIT_FOR_TASK_TOKEN,
      payload: sfn.TaskInput.fromObject({
        taskToken: sfn.JsonPath.taskToken,
        jobId: sfn.JsonPath.stringAt('$.jobId'),
        outputKey: sfn.JsonPath.stringAt('$.output.outputKey'),
        outputBucket: sfn.JsonPath.stringAt('$.output.outputBucket'),
        answerCount: sfn.JsonPath.numberAt('$.output.answerCount'),
      }),
      resultPath: '$.review',
      // No heartbeat timeout — SME reviews may take hours.
      // Add heartbeatTimeout for prod SLA enforcement.
    });

    const definition = parse.next(map).next(write).next(reviewGate);

    const logGroup = new logs.LogGroup(this, 'StateMachineLogGroup', {
      retention: logs.RetentionDays.ONE_MONTH,
    });

    const stateMachine = new sfn.StateMachine(this, 'RfpStateMachine', {
      definitionBody: sfn.DefinitionBody.fromChainable(definition),
      stateMachineType: sfn.StateMachineType.STANDARD,
      tracingEnabled: true,
      logs: { destination: logGroup, level: sfn.LogLevel.ALL },
    });

    this.stateMachineArn = stateMachine.stateMachineArn;

    // Review gate + review API grants
    props.jobsTable.grantReadWriteData(reviewGateFn);
    props.jobsTable.grantReadWriteData(reviewApiFn);
    props.questionsTable.grantReadWriteData(reviewApiFn);
    reviewApiFn.addToRolePolicy(new iam.PolicyStatement({
      actions: ['states:SendTaskSuccess', 'states:SendTaskFailure'],
      resources: ['*'],
    }));

    // HTTP API for the review UI
    const httpApi = new apigatewayv2.HttpApi(this, 'ReviewHttpApi', {
      corsPreflight: {
        allowHeaders: ['Content-Type'],
        allowMethods: [apigatewayv2.CorsHttpMethod.GET, apigatewayv2.CorsHttpMethod.POST, apigatewayv2.CorsHttpMethod.OPTIONS],
        allowOrigins: ['*'],
      },
    });
    const reviewIntegration = new apigatewayv2Integrations.HttpLambdaIntegration('ReviewIntegration', reviewApiFn);
    httpApi.addRoutes({ path: '/reviews',                   methods: [apigatewayv2.HttpMethod.GET],  integration: reviewIntegration });
    httpApi.addRoutes({ path: '/reviews/{jobId}',           methods: [apigatewayv2.HttpMethod.GET],  integration: reviewIntegration });
    httpApi.addRoutes({ path: '/reviews/{jobId}/approve',   methods: [apigatewayv2.HttpMethod.POST], integration: reviewIntegration });
    httpApi.addRoutes({ path: '/reviews/{jobId}/reject',    methods: [apigatewayv2.HttpMethod.POST], integration: reviewIntegration });

    this.reviewApiUrl = httpApi.apiEndpoint;

    // Staleness on-demand trigger — lets the demo fire the sweep mid-session to show the
    // mechanism live without waiting for the 2 AM EventBridge schedule.
    const stalenessIntegration = new apigatewayv2Integrations.HttpLambdaIntegration('StalenessIntegration', stalenessDaemonFn);
    httpApi.addRoutes({ path: '/admin/staleness/trigger', methods: [apigatewayv2.HttpMethod.POST], integration: stalenessIntegration });

    // Mock sources API — simulates Seismic + Gong behind a single Lambda.
    // 5% error rate + 10% tail latency are deliberate (see mock_sources/handler.py).
    const mockSourcesApi = new apigatewayv2.HttpApi(this, 'MockSourcesApi', {
      description: 'Mock Seismic + Gong API for demo',
    });
    const mockIntegration = new apigatewayv2Integrations.HttpLambdaIntegration('MockSourcesIntegration', mockSourcesFn);
    mockSourcesApi.addRoutes({ path: '/seismic/content', methods: [apigatewayv2.HttpMethod.GET], integration: mockIntegration });
    mockSourcesApi.addRoutes({ path: '/gong/calls',      methods: [apigatewayv2.HttpMethod.GET], integration: mockIntegration });

    // Wire the mock sources URL into the retriever so _mock_api_passages() can call it.
    retrieverFn.addEnvironment('MOCK_SOURCES_API_URL', mockSourcesApi.apiEndpoint);

    // EventBridge daily schedule: staleness sweep at 2 AM UTC.
    const stalenessSchedule = new events.Rule(this, 'StalenessSchedule', {
      schedule: events.Schedule.cron({ hour: '2', minute: '0' }),
      description: 'Daily LibraryFeedback staleness sweep',
    });
    stalenessSchedule.addTarget(new targets.LambdaFunction(stalenessDaemonFn));

    new cdk.CfnOutput(this, 'ReviewApiUrl', { value: this.reviewApiUrl });
    new cdk.CfnOutput(this, 'MockSourcesApiUrl', { value: mockSourcesApi.apiEndpoint });
    new cdk.CfnOutput(this, 'StateMachineArn', { value: this.stateMachineArn });
    new cdk.CfnOutput(this, 'GuardrailId', { value: this.guardrailId });

    // ----- Upload API -----
    props.incomingBucket.grantPut(uploadApiFn);
    props.outputBucket.grantRead(uploadApiFn);
    props.jobsTable.grantReadWriteData(uploadApiFn);
    uploadApiFn.addToRolePolicy(new iam.PolicyStatement({
      actions: ['states:StartExecution'],
      resources: [stateMachine.stateMachineArn],
    }));
    uploadApiFn.addEnvironment('INCOMING_BUCKET', props.incomingBucket.bucketName);
    uploadApiFn.addEnvironment('STATE_MACHINE_ARN', stateMachine.stateMachineArn);

    const uploadApi = new apigatewayv2.HttpApi(this, 'UploadApi', {
      corsPreflight: {
        allowHeaders: ['Content-Type'],
        allowMethods: [apigatewayv2.CorsHttpMethod.GET, apigatewayv2.CorsHttpMethod.POST, apigatewayv2.CorsHttpMethod.OPTIONS],
        allowOrigins: ['*'],
      },
    });
    const uploadIntegration = new apigatewayv2Integrations.HttpLambdaIntegration('UploadIntegration', uploadApiFn);
    uploadApi.addRoutes({ path: '/upload/presign',            methods: [apigatewayv2.HttpMethod.POST], integration: uploadIntegration });
    uploadApi.addRoutes({ path: '/upload/{jobId}/start',      methods: [apigatewayv2.HttpMethod.POST], integration: uploadIntegration });
    uploadApi.addRoutes({ path: '/upload/{jobId}/status',     methods: [apigatewayv2.HttpMethod.GET],  integration: uploadIntegration });
    uploadApi.addRoutes({ path: '/upload/{jobId}/download',   methods: [apigatewayv2.HttpMethod.GET],  integration: uploadIntegration });

    new cdk.CfnOutput(this, 'UploadApiUrl', { value: uploadApi.apiEndpoint });
  }
}
