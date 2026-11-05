#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { GmailAssistantStack, PROJECT_TAG_KEY, PROJECT_TAG_VALUE } from '../lib/gmail-assistant-stack';

const app = new cdk.App();

const allowedChatId = app.node.tryGetContext('allowedChatId');
if (!allowedChatId) {
  throw new Error('Missing context value: pass -c allowedChatId=<your Telegram chat id>');
}

const stack = new GmailAssistantStack(app, 'GmailAssistantStack', {
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: process.env.CDK_DEFAULT_REGION ?? 'eu-west-1',
  },
  allowedChatId: String(allowedChatId),
  modelId: app.node.tryGetContext('modelId') ?? 'eu.amazon.nova-pro-v1:0',
  digestTimezone: 'Europe/Rome',
  // 10:00 every day in Rome, DST-aware thanks to EventBridge Scheduler's timezone support.
  digestSchedule: 'cron(0 10 * * ? *)',
  // Off by default. Set BUDGET_EMAIL to create a monthly cost budget that emails that address.
  budgetEmail: process.env.BUDGET_EMAIL || undefined,
  budgetLimitUsd: Number(process.env.BUDGET_LIMIT_USD ?? '10'),
  // Off by default. Set ALARM_EMAIL to create CloudWatch alarms on unusual traffic that email that address.
  alarmEmail: process.env.ALARM_EMAIL || undefined,
  alarmTurnsPerHour: Number(process.env.ALARM_TURNS_PER_HOUR ?? '30'),
  alarmRefusalsPerHour: Number(process.env.ALARM_REFUSALS_PER_HOUR ?? '3'),
  alarmChangesPerDay: Number(process.env.ALARM_CHANGES_PER_DAY ?? '30'),
});

// Every resource that supports tags gets this one, so the cost of the project can be tracked on its own once the tag
// is activated as a cost allocation tag in the Billing console.
cdk.Tags.of(stack).add(PROJECT_TAG_KEY, PROJECT_TAG_VALUE);
