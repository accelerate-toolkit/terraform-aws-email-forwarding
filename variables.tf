variable "name" {
  type = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,29}$", var.name))
    error_message = "Use a unique project-specific lowercase name, 3-30 characters."
  }
}
variable "receipt_rule_set_name" { type = string }
variable "domain" { type = string }
variable "recipient_domain" {
  description = "Exact receiving domain; must equal the module-owned domain. Subdomains are not implicitly included."
  type        = string
}
variable "forwarding_rules" {
  description = "Named rules. Patterns match the entire local part case-insensitively; only * is special. All matching rules apply."
  type        = map(object({ patterns = set(string), forward_to = string }))
  validation {
    condition = length(var.forwarding_rules) > 0 && alltrue([for name, rule in var.forwarding_rules :
      can(regex("^[a-z][a-z0-9_-]*$", name)) && length(rule.patterns) > 0 &&
      alltrue([for pattern in rule.patterns : length(pattern) > 0 && !can(regex("[@[:space:]]", pattern))]) &&
      can(regex("^[^@[:space:]<>]+@[^@[:space:]<>]+$", rule.forward_to))
    ])
    error_message = "Provide named rules with nonempty local-part patterns and one valid destination each."
  }
}
variable "runtime_role_path" {
  type    = string
  default = "/"
  validation {
    condition     = can(regex("^/([!-~]+/)?$", var.runtime_role_path))
    error_message = "Role paths must begin and end with / (or be /)."
  }
}
variable "runtime_permissions_boundary_arn" {
  type    = string
  default = null
}

variable "forward_from" { type = string }
variable "alarm_topic_arn" {
  description = "Existing independently delivered/confirmed alert topic. Must be in this account and region."
  type        = string
}
variable "raw_retention_days" {
  description = "Null disables expiry. Set 30 only after failure alerts and recovery are verified."
  type        = number
  default     = null
  validation {
    condition     = var.raw_retention_days == null ? true : var.raw_retention_days == 30
    error_message = "Use null (no expiry) or the reviewed 30-day retention."
  }
}
variable "observability_verified" {
  type    = bool
  default = false
}
variable "max_message_bytes" {
  type    = number
  default = 35000000
  validation {
    condition     = var.max_message_bytes >= 1024 && var.max_message_bytes <= 35000000
    error_message = "Use 1 KiB to 35 MB, below the SES v2 40 MB limit."
  }
}
