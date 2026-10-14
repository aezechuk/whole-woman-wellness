# Deploying Whole Woman Wellness to AWS

Target: **https://wellness.arthenticarts.org** · Region: **us-east-2** · About an hour end to end.

What gets built (all in `terraform/`):

| Piece | Details |
| --- | --- |
| Server | One EC2 `t4g.small` (Amazon Linux 2023), encrypted disk, IMDSv2 only, **no SSH** (Session Manager instead), only 80/443 open |
| App stack | Docker Compose: Caddy (automatic HTTPS) → Flask/Gunicorn → PostgreSQL 16 (not exposed) |
| Reflections key | KMS key with automatic rotation; **only the app role can decrypt**; account admins can manage but not use it; any other use alerts you |
| Email | SES identity for `wellness.arthenticarts.org` with DKIM; the app can send only as that domain |
| Secrets | SSM Parameter Store (SecureString); never in code, Terraform state, or GitHub |
| Backups | Nightly `pg_dump` to a private, encrypted, write-only S3 bucket; auto-deleted after 14 days |
| Monitoring | CloudTrail (all regions, log validation), GuardDuty and Security Hub with CIS benchmark (optional: `enable_security_services = true`, needs advanced features on project-based accounts), $50/month budget alert, email alerts for GuardDuty findings and journal-key misuse |
| Updates | Security patches applied automatically every day |

## 0. Before you start

This account uses AWS's newer project-based sign-up (projects, team members, monthly spend limits).

- **Owner:** arthenticarts@gmail.com (protect it with Google 2-Step Verification). **Day-to-day:** a team member login (Team → Invite new team member).
- **Spend limit:** set it to at least **$75/month**. If a project hits its limit, AWS *pauses the project and stops its resources*, which would take the app offline mid-program.
- Work in **AWS CloudShell** (the `>_` icon), which is already signed in. Install Terraform there (step 3).
- If Terraform reports that a service isn't supported for this project, turn on the project's **advanced features** (no cost, no downtime) and re-run `terraform apply`.

## 1. Create a read-only GitHub token (2 min)

GitHub → Settings → Developer settings → **Fine-grained tokens** → Generate new token
- Repository access: **Only select repositories** → `whole-woman-wellness`
- Permissions: **Contents: Read-only** (nothing else)
- Expiration: **March 31, 2027**

Copy it; you'll paste it in step 2. The server uses it only to pull code.

## 2. Store the app's secrets (1 min)

```bash
cd deploy
bash scripts/put-secrets.sh        # generates SECRET_KEY + DB password, asks for the GitHub token
```

## 3. Build the infrastructure (5–10 min)

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars   # admin + alert emails are already filled in
terraform init
terraform plan
terraform apply
```

The server boots, pulls the code, and starts the app on its own. Note the outputs:
`server_ip` and `dns_records_for_squarespace`.

**Confirm the alert subscription emails** AWS sends to both alert addresses (SNS "Subscription Confirmation").

## 4. Add DNS records in Squarespace (5 min)

Squarespace → Domains → **arthenticarts.org** → DNS → **Custom records** → Add record, once per row of
`terraform output dns_records_for_squarespace`:

| Type | Host | Value |
| --- | --- | --- |
| A | `wellness` | *server_ip* |
| CNAME | `xxxx._domainkey.wellness` (×3) | `xxxx.dkim.amazonses.com` |
| TXT | `_dmarc.wellness` | `v=DMARC1; p=quarantine; adkim=r; aspf=r` |

Only **add**; don't change the existing website or email-forwarding records.
Within about an hour: Caddy gets the HTTPS certificate on its own, and SES shows the domain as **Verified**.

## 5. Email: get out of the SES sandbox

New SES accounts can only send to addresses you've verified. Two steps:

1. **For testing now:** SES console → Identities → Create identity → Email address → add `a.ezechuk@gmail.com` and `arthenticarts@gmail.com`; click the links AWS emails you.
2. **For launch:** SES console → Account dashboard → **Request production access**:
   - Mail type: **Transactional**
   - Website: `https://wellness.arthenticarts.org`
   - Use case (paste):
     > Transactional email for a grant-funded wellness program run by Arthentic, an Indiana arts nonprofit. About 30 participants who registered for the program sign in with one-time codes and receive at most one email per week (reflection prompts) plus a few program notices through February 2027. Every non-essential email includes a one-click unsubscribe; addresses come only from people who joined with a program code. We monitor bounces and complaints and remove any bouncing address.

   Approval usually takes about a day. Request it as soon as DNS is in.

## 6. Create the cohort and join code (1 min)

AWS console → EC2 → instance `wellness-app` → **Connect** → **Session Manager** → Connect:

```bash
sudo bash /opt/wellness/app/deploy/scripts/create-cohort.sh WIARTS26
```

(Use whatever program code you'll share at registration. You can change it later in the admin area.)

## 7. Smoke test

- Visit `https://wellness.arthenticarts.org` → sign in with your own email + the code → complete onboarding.
- Visit `/admin` → sign in with an admin address → check the sign-in alert email arrived.
- On a real iPhone (Safari) and Android (Chrome): sign in, take part of the assessment, close the app, reopen, confirm it resumed; **Add to Home Screen** and open it from the icon.

## Day-to-day

| Task | How |
| --- | --- |
| Deploy new code | Session Manager → `sudo bash /opt/wellness/app/deploy/scripts/deploy.sh` |
| Logs | `cd /opt/wellness/app/deploy && sudo docker compose --env-file /opt/wellness/.env logs --tail 200 app` |
| Email/cron log | `sudo tail /var/log/wellness-cron.log` |
| Backups | S3 bucket in `terraform output backup_bucket`, folder `db/` |
| Restore test | Download a backup, then `gunzip -c file.sql.gz \| docker compose ... exec -T db psql -U wellness -d wellness` on a **test** database |
| Add/remove an admin | Edit `/wellness/ADMIN_EMAILS` in Parameter Store, then run deploy.sh |

## After the program (Feb 28, 2027)

1. Export the funder report CSVs and testimonials from `/admin`.
2. On the server: `sudo docker compose --env-file /opt/wellness/.env exec -T app flask purge-program-data --confirm`
3. Wait 14 days for backups to expire (or empty the backup bucket), then `terraform destroy`.
4. Schedule deletion of the KMS key happens as part of destroy (30-day window) — after that, any stray copies of reflections are unreadable.
