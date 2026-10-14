output "server_ip" {
  description = "Add an A record: wellness -> this IP"
  value       = aws_eip.app.public_ip
}

output "instance_id" {
  value = aws_instance.app.id
}

output "dns_records_for_squarespace" {
  description = "Add these in Squarespace > DNS > Custom records (Host is relative to arthenticarts.org)"
  value = concat(
    [{ type = "A", host = "wellness", value = aws_eip.app.public_ip }],
    [for t in aws_sesv2_email_identity.app.dkim_signing_attributes[0].tokens : {
      type  = "CNAME"
      host  = "${t}._domainkey.wellness"
      value = "${t}.dkim.amazonses.com"
    }],
    [{ type = "TXT", host = "_dmarc.wellness", value = "v=DMARC1; p=quarantine; adkim=r; aspf=r" }],
  )
}

output "journal_key_arn" {
  value = aws_kms_key.journal.arn
}

output "backup_bucket" {
  value = aws_s3_bucket.backups.bucket
}
