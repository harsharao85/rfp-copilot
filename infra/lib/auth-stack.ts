import * as cdk from 'aws-cdk-lib';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import { Construct } from 'constructs';

export interface AuthStackProps extends cdk.StackProps {
  /**
   * Origin of the deployed static site, e.g. `https://d111111abcdef8.cloudfront.net`.
   *
   * The CloudFront domain is only known after the static-site stack exists, and
   * the static site in turn needs the Cognito identifiers for its generated
   * config — so the two cannot both be construct references without a cycle.
   * The domain is therefore supplied as CDK context (`-c siteUrl=…`) on a second
   * deploy pass. Left undefined, the callbacks are localhost-only, which is
   * enough to deploy but not enough to log in from CloudFront.
   */
  readonly siteUrl?: string;

  /**
   * Hosted-UI prefix. Must be globally unique across all AWS accounts in the
   * region, which is why it defaults to something account-scoped in bin/app.ts.
   */
  readonly domainPrefix: string;
}

/**
 * Cognito user pool + Hosted UI for the reviewer-facing APIs.
 *
 * Both HTTP APIs were previously unauthenticated: anyone holding a URL could
 * enumerate jobs, read every draft answer, and POST /approve — which writes
 * attacker-supplied text into the `sme-approved/` corpus that seeds future
 * answers. Every route on both APIs is now behind the JWT authorizer built
 * from this pool (see orchestration-stack).
 *
 * A Cognito-prefix domain is used deliberately: a custom domain needs an ACM
 * certificate and DNS validation, which can stall a deploy for hours and has
 * no place in a PoC.
 */
export class AuthStack extends cdk.Stack {
  public readonly userPool: cognito.UserPool;
  public readonly userPoolId: string;
  public readonly userPoolClientId: string;
  /** `https://cognito-idp.<region>.amazonaws.com/<poolId>` — the JWT `iss`. */
  public readonly issuerUrl: string;
  /** Hosted UI origin, e.g. `https://<prefix>.auth.<region>.amazoncognito.com`. */
  public readonly hostedUiUrl: string;

  constructor(scope: Construct, id: string, props: AuthStackProps) {
    super(scope, id, props);

    // Self sign-up is off: this is a controlled demo, and the operator creates
    // reviewers by hand (`aws cognito-idp admin-create-user`). An open pool in
    // front of an approve endpoint that feeds the knowledge base would be the
    // same Critical finding wearing a login page.
    const userPool = new cognito.UserPool(this, 'ReviewerPool', {
      userPoolName: `${cdk.Stack.of(this).stackName}-reviewers`,
      selfSignUpEnabled: false,
      signInAliases: { email: true },
      signInCaseSensitive: false,
      standardAttributes: {
        email: { required: true, mutable: true },
      },
      passwordPolicy: {
        minLength: 12,
        requireLowercase: true,
        requireUppercase: true,
        requireDigits: true,
        requireSymbols: true,
      },
      accountRecovery: cognito.AccountRecovery.EMAIL_ONLY,
      // PoC: the pool is disposable along with the rest of the stack.
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });
    this.userPool = userPool;
    this.userPoolId = userPool.userPoolId;
    this.issuerUrl = userPool.userPoolProviderUrl;

    // Reviewer group. Group membership rides in the `cognito:groups` claim, so
    // a later unit can authorise per-route on it without another deploy shape.
    new cognito.CfnUserPoolGroup(this, 'ReviewersGroup', {
      userPoolId: userPool.userPoolId,
      groupName: 'reviewers',
      description: 'SMEs permitted to approve or reject RFP answers',
    });

    // The bare origin is registered as well as the two pages: CloudFront serves
    // upload.html as the default root object, so a reviewer who types the site
    // URL without a path is redirected back to `<origin>/` and Cognito matches
    // callbacks exactly. Omitting it turns the most obvious way to open the
    // demo into a redirect_mismatch error page.
    const origin = props.siteUrl ?? 'http://localhost:8000';
    const callbackUrls = [`${origin}/`, `${origin}/upload.html`, `${origin}/review.html`];

    // Public SPA client: no secret to leak into a static page, authorization
    // code only, and no implicit grant — the browser does the PKCE exchange
    // (S256 challenge in ui/auth.js). Cognito requires PKCE for a public client
    // on the code grant; there is no CloudFormation property for it, so the
    // enforcement is Cognito's and the challenge generation is the page's.
    const client = userPool.addClient('WebClient', {
      userPoolClientName: 'rfp-copilot-web',
      generateSecret: false,
      authFlows: { userSrp: true },
      preventUserExistenceErrors: true,
      oAuth: {
        flows: { authorizationCodeGrant: true, implicitCodeGrant: false },
        scopes: [cognito.OAuthScope.OPENID, cognito.OAuthScope.EMAIL, cognito.OAuthScope.PROFILE],
        callbackUrls,
        logoutUrls: callbackUrls,
      },
      idTokenValidity: cdk.Duration.hours(1),
      accessTokenValidity: cdk.Duration.hours(1),
      refreshTokenValidity: cdk.Duration.days(1),
    });
    this.userPoolClientId = client.userPoolClientId;

    const domain = userPool.addDomain('HostedUi', {
      cognitoDomain: { domainPrefix: props.domainPrefix },
    });
    this.hostedUiUrl = domain.baseUrl();

    new cdk.CfnOutput(this, 'UserPoolId', { value: this.userPoolId });
    new cdk.CfnOutput(this, 'UserPoolClientId', { value: this.userPoolClientId });
    new cdk.CfnOutput(this, 'HostedUiUrl', { value: this.hostedUiUrl });
    new cdk.CfnOutput(this, 'IssuerUrl', { value: this.issuerUrl });
    new cdk.CfnOutput(this, 'CallbackUrls', { value: callbackUrls.join(',') });
  }
}
