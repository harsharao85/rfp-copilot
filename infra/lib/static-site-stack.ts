import * as cdk from 'aws-cdk-lib';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as s3deploy from 'aws-cdk-lib/aws-s3-deployment';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
import * as acm from 'aws-cdk-lib/aws-certificatemanager';
import { Construct } from 'constructs';
import * as path from 'path';

export interface StaticSiteStackProps extends cdk.StackProps {
  domainName: string; // e.g. rfp-copilot.meringue-app.com
}

export class StaticSiteStack extends cdk.Stack {
  public readonly distributionDomainName: string;

  constructor(scope: Construct, id: string, props: StaticSiteStackProps) {
    super(scope, id, props);

    const { domainName } = props;

    const siteBucket = new s3.Bucket(this, 'SiteBucket', {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      autoDeleteObjects: true,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // ACM cert must be in us-east-1 for CloudFront — stack env enforces this.
    const certificate = new acm.Certificate(this, 'SiteCert', {
      domainName,
      validation: acm.CertificateValidation.fromDns(),
    });

    const distribution = new cloudfront.Distribution(this, 'SiteDistribution', {
      defaultBehavior: {
        origin: origins.S3BucketOrigin.withOriginAccessControl(siteBucket),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED, // demo: always fresh
      },
      domainNames: [domainName],
      certificate,
      defaultRootObject: 'upload.html',
      errorResponses: [
        { httpStatus: 403, responseHttpStatus: 200, responsePagePath: '/upload.html' },
        { httpStatus: 404, responseHttpStatus: 200, responsePagePath: '/upload.html' },
      ],
    });

    this.distributionDomainName = distribution.distributionDomainName;

    new s3deploy.BucketDeployment(this, 'DeployUI', {
      sources: [s3deploy.Source.asset(path.join(__dirname, '../../ui'))],
      destinationBucket: siteBucket,
      distribution,
      distributionPaths: ['/*'],
    });

    new cdk.CfnOutput(this, 'CloudFrontDomain', {
      value: distribution.distributionDomainName,
      description: 'Add a CNAME record: rfp-copilot.meringue-app.com -> this value',
    });

    new cdk.CfnOutput(this, 'SiteUrl', {
      value: `https://${domainName}`,
    });
  }
}
