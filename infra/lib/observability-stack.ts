import * as cdk from 'aws-cdk-lib';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import { Construct } from 'constructs';

export interface ObservabilityStackProps extends cdk.StackProps {
  readonly stateMachineArn: string;
  readonly jobsTableName: string;
}

/**
 * CloudWatch dashboard for ops. QuickSight dashboards for leadership
 * A/B reporting are created separately (not CDK-managed).
 */
export class ObservabilityStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: ObservabilityStackProps) {
    super(scope, id, props);

    const dashboard = new cloudwatch.Dashboard(this, 'OpsDashboard', {
      dashboardName: 'rfp-copilot-ops',
    });

    dashboard.addWidgets(
      new cloudwatch.GraphWidget({
        title: 'Step Functions executions (started / succeeded / failed)',
        left: [
          new cloudwatch.Metric({
            namespace: 'AWS/States',
            metricName: 'ExecutionsStarted',
            dimensionsMap: { StateMachineArn: props.stateMachineArn },
            statistic: 'Sum',
          }),
          new cloudwatch.Metric({
            namespace: 'AWS/States',
            metricName: 'ExecutionsSucceeded',
            dimensionsMap: { StateMachineArn: props.stateMachineArn },
            statistic: 'Sum',
          }),
          new cloudwatch.Metric({
            namespace: 'AWS/States',
            metricName: 'ExecutionsFailed',
            dimensionsMap: { StateMachineArn: props.stateMachineArn },
            statistic: 'Sum',
          }),
        ],
        width: 12,
      }),
      new cloudwatch.GraphWidget({
        title: 'DynamoDB jobs table throttles',
        left: [
          new cloudwatch.Metric({
            namespace: 'AWS/DynamoDB',
            metricName: 'ThrottledRequests',
            dimensionsMap: { TableName: props.jobsTableName },
            statistic: 'Sum',
          }),
        ],
        width: 12,
      }),
    );
  }
}
