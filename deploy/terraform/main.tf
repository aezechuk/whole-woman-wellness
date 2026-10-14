data "aws_caller_identity" "me" {}
data "aws_partition" "p" {}

locals {
  account_id = data.aws_caller_identity.me.account_id
  name       = "wellness"
}

# ---------------------------------------------------------------------------
# Network: default VPC, one public instance, only 80/443 open. No SSH (use SSM).
# ---------------------------------------------------------------------------
data "aws_vpc" "default" { default = true }

resource "aws_security_group" "web" {
  name        = "${local.name}-web"
  description = "HTTPS (and HTTP for certificate issuance/redirect) only"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description      = "HTTP (redirects to HTTPS; ACME challenge)"
    from_port        = 80
    to_port          = 80
    protocol         = "tcp"
    cidr_blocks      = ["0.0.0.0/0"]
    ipv6_cidr_blocks = ["::/0"]
  }
  ingress {
    description      = "HTTPS"
    from_port        = 443
    to_port          = 443
    protocol         = "tcp"
    cidr_blocks      = ["0.0.0.0/0"]
    ipv6_cidr_blocks = ["::/0"]
  }
  egress {
    description      = "Outbound (AWS APIs, package updates, GitHub, LetsEncrypt)"
    from_port        = 0
    to_port          = 0
    protocol         = "-1"
    cidr_blocks      = ["0.0.0.0/0"]
    ipv6_cidr_blocks = ["::/0"]
  }
}

# ---------------------------------------------------------------------------
# KMS key for reflections. No break-glass: account admins can manage the key
# (and every change is logged in CloudTrail), but only the app role can
# encrypt/decrypt, and only with a participant-bound encryption context.
# ---------------------------------------------------------------------------
resource "aws_kms_key" "journal" {
  description             = "Envelope key for participant reflections"
  enable_key_rotation     = true
  deletion_window_in_days = 30
  policy                  = data.aws_iam_policy_document.journal_key.json
}

resource "aws_kms_alias" "journal" {
  name          = "alias/${local.name}-journal"
  target_key_id = aws_kms_key.journal.key_id
}

data "aws_iam_policy_document" "journal_key" {
  statement {
    sid    = "AccountAdministersButCannotUseKey"
    effect = "Allow"
    principals {
      type        = "AWS"
      identifiers = ["arn:${data.aws_partition.p.partition}:iam::${local.account_id}:root"]
    }
    actions = [
      "kms:Create*", "kms:Describe*", "kms:Enable*", "kms:List*", "kms:Put*", "kms:Update*",
      "kms:Revoke*", "kms:Disable*", "kms:Get*", "kms:Delete*", "kms:TagResource",
      "kms:UntagResource", "kms:ScheduleKeyDeletion", "kms:CancelKeyDeletion",
    ]
    resources = ["*"]
  }
  statement {
    sid    = "AppRoleUsesKeyForParticipantData"
    effect = "Allow"
    principals {
      type        = "AWS"
      identifiers = [aws_iam_role.app.arn]
    }
    actions   = ["kms:GenerateDataKey", "kms:Decrypt"]
    resources = ["*"]
    condition {
      test     = "Null"
      variable = "kms:EncryptionContext:participant"
      values   = ["false"]
    }
  }
}

# ---------------------------------------------------------------------------
# Backups: write-only from the server, encrypted, private, kept 14 days.
# ---------------------------------------------------------------------------
resource "aws_s3_bucket" "backups" {
  bucket_prefix = "${local.name}-backups-"
  force_destroy = false
}

resource "aws_s3_bucket_public_access_block" "backups" {
  bucket                  = aws_s3_bucket.backups.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "backups" {
  bucket = aws_s3_bucket.backups.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_ownership_controls" "backups" {
  bucket = aws_s3_bucket.backups.id
  rule { object_ownership = "BucketOwnerEnforced" }
}

resource "aws_s3_bucket_lifecycle_configuration" "backups" {
  bucket = aws_s3_bucket.backups.id
  rule {
    id     = "expire-after-14-days"
    status = "Enabled"
    filter {}
    expiration { days = 14 }
  }
}

resource "aws_s3_bucket_policy" "backups_tls_only" {
  bucket = aws_s3_bucket.backups.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyInsecureTransport"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource  = [aws_s3_bucket.backups.arn, "${aws_s3_bucket.backups.arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })
}

# ---------------------------------------------------------------------------
# Email: SES identity for the subdomain with Easy DKIM.
# ---------------------------------------------------------------------------
resource "aws_sesv2_email_identity" "app" {
  email_identity = var.app_host
}

# ---------------------------------------------------------------------------
# Instance role: least privilege. No access keys anywhere.
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "ec2_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "app" {
  name               = "${local.name}-app"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume.json
}

resource "aws_iam_role_policy_attachment" "ssm_core" {
  role       = aws_iam_role.app.name
  policy_arn = "arn:${data.aws_partition.p.partition}:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

data "aws_iam_policy_document" "app" {
  statement {
    sid       = "ReadOwnParameters"
    actions   = ["ssm:GetParametersByPath", "ssm:GetParameters", "ssm:GetParameter"]
    resources = ["arn:${data.aws_partition.p.partition}:ssm:${var.region}:${local.account_id}:parameter/${local.name}/*"]
  }
  statement {
    sid       = "DecryptSecureStringParameters"
    actions   = ["kms:Decrypt"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["ssm.${var.region}.amazonaws.com"]
    }
  }
  statement {
    # SES authorizes against every identity involved in a send: the sender's
    # domain and, while in the sandbox, the verified recipient too. So allow
    # this account's identities, but only when sending FROM the app's domain.
    sid       = "SendAsAppDomainOnly"
    actions   = ["ses:SendEmail", "ses:SendRawEmail"]
    resources = ["arn:${data.aws_partition.p.partition}:ses:${var.region}:${local.account_id}:identity/*"]
    condition {
      test     = "StringLike"
      variable = "ses:FromAddress"
      values   = ["*@${var.app_host}"]
    }
  }
  statement {
    sid       = "WriteBackupsOnly"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.backups.arn}/db/*"]
  }
}

resource "aws_iam_role_policy" "app" {
  name   = "${local.name}-app"
  role   = aws_iam_role.app.id
  policy = data.aws_iam_policy_document.app.json
}

resource "aws_iam_instance_profile" "app" {
  name = "${local.name}-app"
  role = aws_iam_role.app.name
}

# ---------------------------------------------------------------------------
# Non-secret configuration. Secrets (SECRET_KEY, POSTGRES_PASSWORD) are created
# by scripts/put-secrets.sh so they never land in Terraform state.
# ---------------------------------------------------------------------------
resource "aws_ssm_parameter" "config" {
  for_each = {
    ADMIN_EMAILS        = var.admin_emails
    JOURNAL_KMS_KEY_ID  = aws_kms_key.journal.arn
    PUBLIC_URL          = "https://${var.app_host}"
    MAIL_FROM           = "Radical Self-Care Reset <hello@${var.app_host}>"
    CONTACT_EMAIL       = var.contact_email
    SITE_HOST           = var.app_host
    BACKUP_BUCKET       = aws_s3_bucket.backups.bucket
    REPO_URL            = var.repo_url
  }
  name  = "/${local.name}/${each.key}"
  type  = "String"
  value = each.value
}

# ---------------------------------------------------------------------------
# The server: Amazon Linux 2023 (arm64), IMDSv2 only, encrypted disk, no SSH key.
# ---------------------------------------------------------------------------
data "aws_ssm_parameter" "al2023_arm" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"
}

resource "aws_instance" "app" {
  ami                    = data.aws_ssm_parameter.al2023_arm.value
  instance_type          = var.instance_type
  iam_instance_profile   = aws_iam_instance_profile.app.name
  vpc_security_group_ids = [aws_security_group.web.id]
  user_data              = file("${path.module}/../scripts/bootstrap.sh")

  metadata_options {
    http_tokens                 = "required"
    http_put_response_hop_limit = 2 # containers reach the instance role through one extra hop
  }

  root_block_device {
    volume_type = "gp3"
    volume_size = 20
    encrypted   = true
  }

  lifecycle {
    ignore_changes = [ami, user_data]
  }

  # Config must exist before first boot so bootstrap.sh can deploy right away.
  depends_on = [aws_ssm_parameter.config, aws_iam_role_policy.app]

  tags = { Name = "${local.name}-app" }
}

resource "aws_eip" "app" {
  instance = aws_instance.app.id
  domain   = "vpc"
}

# ---------------------------------------------------------------------------
# Detection and cost guardrails.
# ---------------------------------------------------------------------------
resource "aws_s3_bucket" "trail" {
  bucket_prefix = "${local.name}-cloudtrail-"
  force_destroy = false
}

resource "aws_s3_bucket_public_access_block" "trail" {
  bucket                  = aws_s3_bucket.trail.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "trail" {
  bucket = aws_s3_bucket.trail.id
  rule {
    id     = "expire-after-365-days"
    status = "Enabled"
    filter {}
    expiration { days = 365 }
  }
}

resource "aws_s3_bucket_policy" "trail" {
  bucket = aws_s3_bucket.trail.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AWSCloudTrailAclCheck"
        Effect    = "Allow"
        Principal = { Service = "cloudtrail.amazonaws.com" }
        Action    = "s3:GetBucketAcl"
        Resource  = aws_s3_bucket.trail.arn
      },
      {
        Sid       = "AWSCloudTrailWrite"
        Effect    = "Allow"
        Principal = { Service = "cloudtrail.amazonaws.com" }
        Action    = "s3:PutObject"
        Resource  = "${aws_s3_bucket.trail.arn}/AWSLogs/${local.account_id}/*"
        Condition = { StringEquals = { "s3:x-amz-acl" = "bucket-owner-full-control" } }
      },
      {
        Sid       = "DenyInsecureTransport"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource  = [aws_s3_bucket.trail.arn, "${aws_s3_bucket.trail.arn}/*"]
        Condition = { Bool = { "aws:SecureTransport" = "false" } }
      },
    ]
  })
}

resource "aws_cloudtrail" "main" {
  name                          = "${local.name}-trail"
  s3_bucket_name                = aws_s3_bucket.trail.id
  is_multi_region_trail         = true
  include_global_service_events = true
  enable_log_file_validation    = true
  depends_on                    = [aws_s3_bucket_policy.trail]
}

# GuardDuty and Security Hub are off by default: the AWS project-based account
# experience blocks them (service control policy) until advanced features are
# enabled. Turn on with enable_security_services = true in terraform.tfvars.
resource "aws_guardduty_detector" "main" {
  count  = var.enable_security_services ? 1 : 0
  enable = true
}

resource "aws_securityhub_account" "main" {
  count = var.enable_security_services ? 1 : 0
}

resource "aws_securityhub_standards_subscription" "cis" {
  count         = var.enable_security_services ? 1 : 0
  standards_arn = "arn:${data.aws_partition.p.partition}:securityhub:${var.region}::standards/cis-aws-foundations-benchmark/v/3.0.0"
  depends_on    = [aws_securityhub_account.main]
}

resource "aws_budgets_budget" "monthly" {
  name         = "${local.name}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = var.alert_emails
  }
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = var.alert_emails
  }
}

# Alert when anything other than the app tries to use the reflection key.
resource "aws_cloudwatch_event_rule" "journal_key_misuse" {
  name        = "${local.name}-journal-key-misuse"
  description = "KMS Decrypt/GenerateDataKey on the journal key by anyone but the app role"
  event_pattern = jsonencode({
    source        = ["aws.kms"]
    "detail-type" = ["AWS API Call via CloudTrail"]
    detail = {
      eventName = ["Decrypt", "GenerateDataKey", "PutKeyPolicy", "ScheduleKeyDeletion", "DisableKey"]
      resources = { ARN = [aws_kms_key.journal.arn] }
      "$or" = [
        { userIdentity = { type = ["Root", "IAMUser"] } },
        { userIdentity = { sessionContext = { sessionIssuer = { arn = [{ "anything-but" = aws_iam_role.app.arn }] } } } },
      ]
    }
  })
}

resource "aws_sns_topic" "alerts" {
  name = "${local.name}-alerts"
}

resource "aws_sns_topic_policy" "alerts" {
  arn = aws_sns_topic.alerts.arn
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "events.amazonaws.com" }
      Action    = "sns:Publish"
      Resource  = aws_sns_topic.alerts.arn
    }]
  })
}

resource "aws_sns_topic_subscription" "alerts" {
  for_each  = toset(var.alert_emails)
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = each.value
}

resource "aws_cloudwatch_event_target" "journal_key_misuse" {
  rule = aws_cloudwatch_event_rule.journal_key_misuse.name
  arn  = aws_sns_topic.alerts.arn
}

resource "aws_cloudwatch_event_rule" "guardduty" {
  name = "${local.name}-guardduty-findings"
  event_pattern = jsonencode({
    source        = ["aws.guardduty"]
    "detail-type" = ["GuardDuty Finding"]
    detail        = { severity = [{ numeric = [">=", 4] }] }
  })
}

resource "aws_cloudwatch_event_target" "guardduty" {
  rule = aws_cloudwatch_event_rule.guardduty.name
  arn  = aws_sns_topic.alerts.arn
}
