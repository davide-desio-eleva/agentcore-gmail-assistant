import * as cdk from 'aws-cdk-lib';
import * as bedrock from 'aws-cdk-lib/aws-bedrock';
import * as bedrockagentcore from 'aws-cdk-lib/aws-bedrockagentcore';
import * as budgets from 'aws-cdk-lib/aws-budgets';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import * as cloudwatchActions from 'aws-cdk-lib/aws-cloudwatch-actions';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as ecrAssets from 'aws-cdk-lib/aws-ecr-assets';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as scheduler from 'aws-cdk-lib/aws-scheduler';
import * as sns from 'aws-cdk-lib/aws-sns';
import * as subscriptions from 'aws-cdk-lib/aws-sns-subscriptions';
import { Construct } from 'constructs';
import * as path from 'path';

// SSM Parameter Store names. CloudFormation can only create plain String parameters, so the
// SecureString values are written by scripts/put_secrets.py and scripts/gmail_oauth_setup.py.
// They are not part of this stack, so scripts/teardown.py deletes them explicitly.
export const PARAM_PREFIX = '/gmail-assistant';
export const TELEGRAM_BOT_TOKEN_PARAM = `${PARAM_PREFIX}/telegram-bot-token`;
export const TELEGRAM_WEBHOOK_SECRET_PARAM = `${PARAM_PREFIX}/telegram-webhook-secret`;
export const GMAIL_OAUTH_PARAM = `${PARAM_PREFIX}/gmail-oauth`;
// Fixed name, so the agent can schedule reminders for the worker without a circular reference.
export const WORKER_FUNCTION_NAME = 'gmail-assistant-worker';
// The tag on every resource of the stack, and the filter of the optional budget.
export const PROJECT_TAG_KEY = 'project';
export const PROJECT_TAG_VALUE = 'gmail-assistant';

export interface GmailAssistantStackProps extends cdk.StackProps {
  allowedChatId: string;
  modelId: string;
  digestTimezone: string;
  digestSchedule: string;
  // The optional cost budget. Without an email, no budget is created.
  budgetEmail?: string;
  budgetLimitUsd?: number;
  // The optional CloudWatch alarms. Without an email, no alarm is created.
  alarmEmail?: string;
  alarmTurnsPerHour?: number;
  alarmRefusalsPerHour?: number;
  alarmChangesPerDay?: number;
}

export class GmailAssistantStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: GmailAssistantStackProps) {
    super(scope, id, props);

    // Read access to the SecureString parameters that one principal needs, and nothing else. The AWS managed key
    // aws/ssm decrypts them: the decrypt is allowed only through SSM, and only for these parameters, because SSM
    // puts the ARN of the parameter in the encryption context of the call.
    const readParameters = (names: string[]): iam.PolicyStatement[] => {
      const arns = names.map((name) => `arn:${this.partition}:ssm:${this.region}:${this.account}:parameter${name}`);
      return [
        new iam.PolicyStatement({ actions: ['ssm:GetParameter'], resources: arns }),
        new iam.PolicyStatement({
          actions: ['kms:Decrypt'],
          resources: [`arn:${this.partition}:kms:${this.region}:${this.account}:key/*`],
          conditions: {
            StringEquals: {
              'kms:ViaService': `ssm.${this.region}.amazonaws.com`,
              'kms:EncryptionContext:PARAMETER_ARN': arns,
            },
          },
        }),
      ];
    };

    // ---------- Guardrail for the text of emails ----------
    // It is not attached to the model. The agent applies it to the text of each email with the ApplyGuardrail API,
    // before the text goes anywhere else, and treats a hit as a signal.
    const guardrail = new bedrock.CfnGuardrail(this, 'EmailTextGuardrail', {
      name: 'gmail_assistant_email_text',
      description: 'Flags text in an email that tries to give orders to an AI assistant.',
      blockedInputMessaging: 'This text was flagged by the guardrail.',
      blockedOutputsMessaging: 'This text was flagged by the guardrail.',
      contentPolicyConfig: {
        filtersConfig: [{ type: 'PROMPT_ATTACK', inputStrength: 'HIGH', outputStrength: 'NONE' }],
      },
    });
    const guardrailVersion = new bedrock.CfnGuardrailVersion(this, 'EmailTextGuardrailVersion', {
      guardrailIdentifier: guardrail.attrGuardrailId,
    });

    // ---------- Log of mailbox actions, so the last one can be undone ----------
    const actionLog = new dynamodb.Table(this, 'ActionLog', {
      partitionKey: { name: 'owner', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'ts', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      // The webhook stores the id of each Telegram update it has handled, and the item expires after a day.
      timeToLiveAttribute: 'expires',
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // ---------- Audit trail ----------
    // What the guard decided: each change, and each time it refused one that nobody asked for. The runtime can add lines
    // and cannot alter or delete them. A year of retention, because an audit trail that expires in a month is not one.
    const auditLogs = new logs.LogGroup(this, 'AuditLogs', {
      logGroupName: '/gmail-assistant/audit',
      retention: logs.RetentionDays.ONE_YEAR,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // Used by the digest schedule and by the reminders the agent creates.
    const schedulerRole = new iam.Role(this, 'DigestSchedulerRole', {
      assumedBy: new iam.ServicePrincipal('scheduler.amazonaws.com', {
        conditions: { StringEquals: { 'aws:SourceAccount': this.account } },
      }),
    });
    const workerArn = `arn:${this.partition}:lambda:${this.region}:${this.account}:function:${WORKER_FUNCTION_NAME}`;

    // ---------- AgentCore Runtime (Strands agent) ----------
    const agentImage = new ecrAssets.DockerImageAsset(this, 'AgentImage', {
      directory: path.join(__dirname, '../../agent'),
      platform: ecrAssets.Platform.LINUX_ARM64,
    });

    const runtimeRole = new iam.Role(this, 'AgentRuntimeRole', {
      assumedBy: new iam.ServicePrincipal('bedrock-agentcore.amazonaws.com', {
        conditions: {
          StringEquals: { 'aws:SourceAccount': this.account },
          ArnLike: { 'aws:SourceArn': `arn:${this.partition}:bedrock-agentcore:${this.region}:${this.account}:*` },
        },
      }),
    });
    agentImage.repository.grantPull(runtimeRole);
    runtimeRole.addToPolicy(new iam.PolicyStatement({
      actions: ['bedrock:ApplyGuardrail'],
      resources: [guardrail.attrGuardrailArn],
    }));
    // The agent gets, puts, deletes and queries items. It cannot scan, batch-write or change the table.
    actionLog.grant(runtimeRole, 'dynamodb:GetItem', 'dynamodb:PutItem', 'dynamodb:DeleteItem', 'dynamodb:Query');
    runtimeRole.addToPolicy(new iam.PolicyStatement({
      actions: ['logs:CreateLogStream', 'logs:PutLogEvents'],
      resources: [auditLogs.logGroupArn],
    }));
    runtimeRole.addToPolicy(new iam.PolicyStatement({
      actions: ['scheduler:CreateSchedule'],
      resources: [`arn:${this.partition}:scheduler:${this.region}:${this.account}:schedule/default/gmail-assistant-reminder-*`],
    }));
    runtimeRole.addToPolicy(new iam.PolicyStatement({
      // ListSchedules does not support resource-level permissions: a schedule-group ARN is denied.
      actions: ['scheduler:ListSchedules'],
      resources: ['*'],
    }));
    runtimeRole.addToPolicy(new iam.PolicyStatement({
      actions: ['scheduler:GetSchedule', 'scheduler:DeleteSchedule'],
      resources: [`arn:${this.partition}:scheduler:${this.region}:${this.account}:schedule/default/gmail-assistant-reminder-*`],
    }));
    runtimeRole.addToPolicy(new iam.PolicyStatement({
      actions: ['iam:PassRole'],
      resources: [schedulerRole.roleArn],
      conditions: { StringEquals: { 'iam:PassedToService': 'scheduler.amazonaws.com' } },
    }));
    readParameters([GMAIL_OAUTH_PARAM, TELEGRAM_BOT_TOKEN_PARAM]).forEach((statement) => runtimeRole.addToPolicy(statement));
    runtimeRole.addToPolicy(new iam.PolicyStatement({
      actions: ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream'],
      // The one model that the stack is configured with. A geo profile such as eu.amazon.nova-pro-v1:0 routes the call to
      // the model in several regions of the geography, so the foundation model has to be allowed in each of them.
      resources: [
        `arn:${this.partition}:bedrock:*::foundation-model/${props.modelId.replace(/^(eu|us|apac|jp|global)\./, '')}`,
        `arn:${this.partition}:bedrock:${this.region}:${this.account}:inference-profile/${props.modelId}`,
      ],
    }));
    runtimeRole.addToPolicy(new iam.PolicyStatement({
      actions: ['logs:DescribeLogStreams', 'logs:CreateLogGroup'],
      resources: [`arn:${this.partition}:logs:${this.region}:${this.account}:log-group:/aws/bedrock-agentcore/runtimes/*`],
    }));
    runtimeRole.addToPolicy(new iam.PolicyStatement({
      actions: ['logs:CreateLogStream', 'logs:PutLogEvents'],
      resources: [`arn:${this.partition}:logs:${this.region}:${this.account}:log-group:/aws/bedrock-agentcore/runtimes/*:log-stream:*`],
    }));
    runtimeRole.addToPolicy(new iam.PolicyStatement({
      actions: ['xray:PutTraceSegments', 'xray:PutTelemetryRecords', 'xray:GetSamplingRules', 'xray:GetSamplingTargets'],
      resources: ['*'],
    }));
    runtimeRole.addToPolicy(new iam.PolicyStatement({
      actions: ['cloudwatch:PutMetricData'],
      resources: ['*'],
      conditions: { StringEquals: { 'cloudwatch:namespace': 'bedrock-agentcore' } },
    }));
    runtimeRole.addToPolicy(new iam.PolicyStatement({
      actions: [
        'bedrock-agentcore:GetWorkloadAccessToken',
        'bedrock-agentcore:GetWorkloadAccessTokenForJWT',
        'bedrock-agentcore:GetWorkloadAccessTokenForUserId',
      ],
      resources: [
        `arn:${this.partition}:bedrock-agentcore:${this.region}:${this.account}:workload-identity-directory/default`,
        `arn:${this.partition}:bedrock-agentcore:${this.region}:${this.account}:workload-identity-directory/default/workload-identity/*`,
      ],
    }));

    // ---------- Long-term memory ----------
    // The namespace must match agent/gmail_assistant/memory.py. Only the preferences are extracted: an episodic
    // strategy would keep summaries of what was said, and nothing reads them back. The events of the chat are kept
    // for the minimum, seven days.
    const memory = new bedrockagentcore.CfnMemory(this, 'AssistantMemory', {
      name: 'gmail_assistant_memory',
      description: 'Preferences of the mailbox owner.',
      eventExpiryDuration: 7,
      memoryStrategies: [
        {
          userPreferenceMemoryStrategy: {
            name: 'preferences',
            namespaces: ['/users/{actorId}/preferences'],
          },
        },
      ],
    });

    // Only what the agent does with its memory, and only on this memory resource. The session manager writes and
    // reads the events of the chat and retrieves the preferences. The memory tools list the records and delete one.
    // Nothing here can change the memory resource itself, its strategies, or create another memory.
    runtimeRole.addToPolicy(new iam.PolicyStatement({
      actions: [
        'bedrock-agentcore:CreateEvent',
        'bedrock-agentcore:GetEvent',
        'bedrock-agentcore:ListEvents',
        'bedrock-agentcore:DeleteEvent',
        'bedrock-agentcore:RetrieveMemoryRecords',
        'bedrock-agentcore:ListMemoryRecords',
        'bedrock-agentcore:DeleteMemoryRecord',
      ],
      resources: [memory.attrMemoryArn],
    }));

    const agentRuntime = new bedrockagentcore.CfnRuntime(this, 'AgentRuntime', {
      agentRuntimeName: 'gmail_assistant',
      description: 'Strands-based Gmail assistant reachable through Telegram.',
      roleArn: runtimeRole.roleArn,
      agentRuntimeArtifact: {
        containerConfiguration: { containerUri: agentImage.imageUri },
      },
      networkConfiguration: { networkMode: 'PUBLIC' },
      protocolConfiguration: 'HTTP',
      environmentVariables: {
        MODEL_ID: props.modelId,
        GMAIL_PARAM_NAME: GMAIL_OAUTH_PARAM,
        TELEGRAM_BOT_TOKEN_PARAM,
        ALLOWED_CHAT_ID: props.allowedChatId,
        MEMORY_ID: memory.attrMemoryId,
        GUARDRAIL_ID: guardrail.attrGuardrailId,
        GUARDRAIL_VERSION: guardrailVersion.attrVersion,
        ACTION_LOG_TABLE: actionLog.tableName,
        AUDIT_LOG_GROUP: auditLogs.logGroupName,
        WORKER_FUNCTION_ARN: workerArn,
        SCHEDULER_ROLE_ARN: schedulerRole.roleArn,
        TIMEZONE: props.digestTimezone,
      },
    });
    // The runtime is validated against the role at creation time, so wait for its policy.
    agentRuntime.node.addDependency(runtimeRole.node.findChild('DefaultPolicy'));

    // AgentCore creates the runtime's log group itself, with no expiry. This sets one, and creates the group if it is
    // not there yet. The agent does not print the model's output, but errors and traces still land here.
    new logs.LogRetention(this, 'RuntimeLogRetention', {
      logGroupName: `/aws/bedrock-agentcore/runtimes/${agentRuntime.attrAgentRuntimeId}-DEFAULT`,
      retention: logs.RetentionDays.ONE_MONTH,
    });

    // ---------- Telegram plumbing ----------
    const lambdaCode = lambda.Code.fromAsset(path.join(__dirname, '../../lambdas'), {
      exclude: ['__pycache__', '.pytest_cache', 'test_*.py'],
    });
    const commonEnv = {
      ALLOWED_CHAT_ID: props.allowedChatId,
      TELEGRAM_BOT_TOKEN_PARAM,
      TIMEZONE: props.digestTimezone,
    };

    // Runs agent turns and sends the answer to Telegram. Invoked by the webhook or the scheduler only.
    const worker = new lambda.Function(this, 'WorkerFunction', {
      runtime: lambda.Runtime.PYTHON_3_13,
      architecture: lambda.Architecture.ARM_64,
      handler: 'worker.handler',
      code: lambdaCode,
      memorySize: 512,
      timeout: cdk.Duration.minutes(15),
      // A failed agent turn must not be retried automatically, or the user gets duplicate replies.
      functionName: WORKER_FUNCTION_NAME,
      retryAttempts: 0,
      // One agent turn at a time: the owner is one person, and two turns on one session would race. A second event
      // waits in the Lambda queue, for at most 30 minutes, instead of running next to the first.
      reservedConcurrentExecutions: 1,
      maxEventAge: cdk.Duration.minutes(30),
      logGroup: new logs.LogGroup(this, 'WorkerLogs', {
        retention: logs.RetentionDays.ONE_MONTH,
        removalPolicy: cdk.RemovalPolicy.DESTROY,
      }),
      environment: {
        ...commonEnv,
        AGENT_RUNTIME_ARN: agentRuntime.attrAgentRuntimeArn,
      },
    });
    readParameters([TELEGRAM_BOT_TOKEN_PARAM]).forEach((statement) => worker.addToRolePolicy(statement));
    worker.addToRolePolicy(new iam.PolicyStatement({
      actions: ['bedrock-agentcore:InvokeAgentRuntime'],
      resources: [agentRuntime.attrAgentRuntimeArn, `${agentRuntime.attrAgentRuntimeArn}/*`],
    }));

    // Public Function URL: Telegram calls it, and the handler rejects anything without the secret token.
    const webhook = new lambda.Function(this, 'WebhookFunction', {
      runtime: lambda.Runtime.PYTHON_3_13,
      architecture: lambda.Architecture.ARM_64,
      handler: 'webhook.handler',
      code: lambdaCode,
      memorySize: 256,
      timeout: cdk.Duration.seconds(10),
      // A cap on a flood against the public URL: the requests above it are throttled and never reach the model.
      reservedConcurrentExecutions: 5,
      logGroup: new logs.LogGroup(this, 'WebhookLogs', {
        retention: logs.RetentionDays.ONE_MONTH,
        removalPolicy: cdk.RemovalPolicy.DESTROY,
      }),
      environment: {
        ...commonEnv,
        WORKER_FUNCTION_NAME: worker.functionName,
        WEBHOOK_SECRET_PARAM: TELEGRAM_WEBHOOK_SECRET_PARAM,
        ACTION_LOG_TABLE: actionLog.tableName,
      },
    });
    readParameters([TELEGRAM_BOT_TOKEN_PARAM, TELEGRAM_WEBHOOK_SECRET_PARAM]).forEach((statement) => webhook.addToRolePolicy(statement));
    // The only thing the webhook writes: the id of an update it is about to handle.
    actionLog.grant(webhook, 'dynamodb:PutItem');
    worker.grantInvoke(webhook);

    const webhookUrl = webhook.addFunctionUrl({
      authType: lambda.FunctionUrlAuthType.NONE,
    });

    // ---------- Daily digest at 10:00 Europe/Rome ----------
    worker.grantInvoke(schedulerRole);

    new scheduler.CfnSchedule(this, 'DailyDigestSchedule', {
      name: 'gmail-assistant-daily-digest',
      description: 'Sends the morning digest of unread emails to Telegram.',
      scheduleExpression: props.digestSchedule,
      scheduleExpressionTimezone: props.digestTimezone,
      flexibleTimeWindow: { mode: 'OFF' },
      target: {
        arn: worker.functionArn,
        roleArn: schedulerRole.roleArn,
        input: JSON.stringify({ type: 'daily_digest' }),
        // Scheduler retries for up to 24h by default, which would send the digest many times.
        retryPolicy: { maximumRetryAttempts: 2, maximumEventAgeInSeconds: 3600 },
      },
    });

    // ---------- Optional cost budget ----------
    // A detective control, not a defence: the caps above stop a flood, the budget says, hours later, that something got
    // through anyway. It counts only the costs that carry the project tag, so the tag has to be activated as a cost
    // allocation tag in the Billing console first. Monitoring budgets and their emails are free.
    if (props.budgetEmail) {
      const subscribers = [{ subscriptionType: 'EMAIL', address: props.budgetEmail }];
      new budgets.CfnBudget(this, 'CostBudget', {
        budget: {
          budgetName: 'gmail-assistant-monthly',
          budgetType: 'COST',
          timeUnit: 'MONTHLY',
          budgetLimit: { amount: props.budgetLimitUsd ?? 10, unit: 'USD' },
          costFilters: { TagKeyValue: [`user:${PROJECT_TAG_KEY}$${PROJECT_TAG_VALUE}`] },
        },
        notificationsWithSubscribers: [
          {
            notification: { notificationType: 'ACTUAL', comparisonOperator: 'GREATER_THAN', threshold: 80, thresholdType: 'PERCENTAGE' },
            subscribers,
          },
          {
            notification: { notificationType: 'FORECASTED', comparisonOperator: 'GREATER_THAN', threshold: 100, thresholdType: 'PERCENTAGE' },
            subscribers,
          },
        ],
      });
    }

    // ---------- Optional alarms ----------
    // The fast signal the budget cannot be: CloudWatch metrics arrive in minutes. Without an email, nothing is created.
    if (props.alarmEmail) {
      const alarmTopic = new sns.Topic(this, 'AlarmTopic', { displayName: 'gmail-assistant alarms' });
      alarmTopic.addSubscription(new subscriptions.EmailSubscription(props.alarmEmail));
      const notify = new cloudwatchActions.SnsAction(alarmTopic);

      // Every agent turn is a worker invocation, whoever started it: chat, digest or reminder.
      new cloudwatch.Alarm(this, 'AgentTurnsAlarm', {
        alarmName: 'gmail-assistant-agent-turns',
        alarmDescription: 'More agent turns in an hour than one person would start.',
        metric: worker.metricInvocations({ period: cdk.Duration.hours(1), statistic: 'Sum' }),
        threshold: props.alarmTurnsPerHour ?? 30,
        evaluationPeriods: 1,
        comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
        treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
      }).addAlarmAction(notify);

      // The webhook reached its concurrency cap: someone is flooding the public URL.
      new cloudwatch.Alarm(this, 'WebhookThrottlesAlarm', {
        alarmName: 'gmail-assistant-webhook-throttles',
        alarmDescription: 'The webhook was throttled, so it received more requests than Telegram sends.',
        metric: webhook.metricThrottles({ period: cdk.Duration.minutes(5), statistic: 'Sum' }),
        threshold: 0,
        evaluationPeriods: 1,
        comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
        treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
      }).addAlarmAction(notify);

      // Someone other than the owner wrote to the bot. The webhook logs it without the text.
      const rejected = new logs.MetricFilter(this, 'RejectedMessagesFilter', {
        logGroup: webhook.logGroup,
        filterPattern: logs.FilterPattern.stringValue('$.event', '=', 'rejected_message'),
        metricNamespace: 'GmailAssistant',
        metricName: 'RejectedMessages',
        metricValue: '1',
      });
      new cloudwatch.Alarm(this, 'RejectedMessagesAlarm', {
        alarmName: 'gmail-assistant-rejected-messages',
        alarmDescription: 'A message from a chat other than the owner reached the webhook.',
        metric: rejected.metric({ period: cdk.Duration.minutes(5), statistic: 'Sum' }),
        threshold: 0,
        evaluationPeriods: 1,
        comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
        treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
      }).addAlarmAction(notify);
    }

    // ---------- Optional alarms on the audit trail ----------
    // The signal that an agent is acting outside what it was asked. A refusal for a change that nobody asked for, or
    // above the cap, is the guard stopping it. A refusal for an email that was not listed is a model hiccup and is
    // not counted. Created only with an alarm email.
    if (props.alarmEmail) {
      // The topic of the alarms above, so there is one subscription to confirm.
      const notifyAudit = new cloudwatchActions.SnsAction(this.node.findChild('AlarmTopic') as sns.Topic);

      const refusals = new logs.MetricFilter(this, 'GuardRefusalsFilter', {
        logGroup: auditLogs,
        filterPattern: logs.FilterPattern.literal('{ $.event = "refused" && $.reason != "unseen" }'),
        metricNamespace: 'GmailAssistant',
        metricName: 'GuardRefusals',
        metricValue: '1',
      });
      new cloudwatch.Alarm(this, 'GuardRefusalsAlarm', {
        alarmName: 'gmail-assistant-guard-refusals',
        alarmDescription: 'The guard refused changes that nobody asked for, more often than a model slip would explain.',
        metric: refusals.metric({ period: cdk.Duration.hours(1), statistic: 'Sum' }),
        threshold: props.alarmRefusalsPerHour ?? 3,
        evaluationPeriods: 1,
        comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
        treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
      }).addAlarmAction(notifyAudit);

      const changes = new logs.MetricFilter(this, 'ExecutedChangesFilter', {
        logGroup: auditLogs,
        filterPattern: logs.FilterPattern.stringValue('$.event', '=', 'executed'),
        metricNamespace: 'GmailAssistant',
        metricName: 'ExecutedChanges',
        metricValue: '$.count',
      });
      new cloudwatch.Alarm(this, 'ExecutedChangesAlarm', {
        alarmName: 'gmail-assistant-executed-changes',
        alarmDescription: 'More changes in a day than one person would ask for.',
        metric: changes.metric({ period: cdk.Duration.days(1), statistic: 'Sum' }),
        threshold: props.alarmChangesPerDay ?? 30,
        evaluationPeriods: 1,
        comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
        treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
      }).addAlarmAction(notifyAudit);
    }

    // ---------- Outputs ----------
    new cdk.CfnOutput(this, 'WebhookUrl', {
      value: webhookUrl.url,
      description: 'Pass this to scripts/set_telegram_webhook.py',
    });
    new cdk.CfnOutput(this, 'AgentRuntimeArn', { value: agentRuntime.attrAgentRuntimeArn });
  }
}
