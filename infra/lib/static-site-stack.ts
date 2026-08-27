import * as cdk from 'aws-cdk-lib';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as s3deploy from 'aws-cdk-lib/aws-s3-deployment';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
import { Construct } from 'constructs';
import * as path from 'path';

/**
 * Static UI on CloudFront + S3, served from the CloudFront-issued domain.
 *
 * No custom domain and no ACM certificate: DNS validation can stall a deploy
 * for hours, and the PoC has no domain of its own to validate against. Point a
 * CNAME at the CloudFrontDomain output later if a branded URL is wanted — that
 * is an additive change, not a rework.
 */
export class StaticSiteStack extends cdk.Stack {
  public readonly distributionDomainName: string;

  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    const siteBucket = new s3.Bucket(this, 'SiteBucket', {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      autoDeleteObjects: true,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    const distribution = new cloudfront.Distribution(this, 'SiteDistribution', {
      defaultBehavior: {
        origin: origins.S3BucketOrigin.withOriginAccessControl(siteBucket),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED, // demo: always fresh
      },
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
    });

    new cdk.CfnOutput(this, 'SiteUrl', {
      value: `https://${distribution.distributionDomainName}`,
    });
  }
}
