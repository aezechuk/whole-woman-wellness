variable "region" {
  type    = string
  default = "us-east-2"
}

variable "app_host" {
  description = "Public hostname for the app"
  type        = string
  default     = "wellness.arthenticarts.org"
}

variable "admin_emails" {
  description = "Admin allowlist (comma-separated). Only these addresses can open /admin."
  type        = string
}

variable "sender_email" {
  description = "From address on every app email. Its domain is verified in SES."
  type        = string
  default     = "arts@arthenticarts.org"
}

variable "sender_name" {
  description = "Display name on every app email"
  type        = string
  default     = "Radical Self-Care Reset"
}

variable "contact_email" {
  description = "Where participants' replies go (Reply-To on every email)"
  type        = string
  default     = "arthenticarts@gmail.com"
}

variable "alert_emails" {
  description = "Who gets budget and security alerts"
  type        = list(string)
}

variable "monthly_budget_usd" {
  type    = number
  default = 50
}

variable "instance_type" {
  type    = string
  default = "t4g.small"
}

variable "repo_url" {
  description = "HTTPS clone URL for the app repo (cloned with a read-only token from SSM)"
  type        = string
  default     = "https://github.com/aezechuk/whole-woman-wellness.git"
}

variable "enable_security_services" {
  description = "Enable GuardDuty and Security Hub (CIS). Needs advanced features on project-based AWS accounts."
  type        = bool
  default     = false
}
