terraform {
  required_version = ">= 1.13.5, < 2.0"
  required_providers {
    nebius = {
      source  = "nebius/nebius"
      version = "0.6.64"
    }
  }
}

provider "nebius" {
  profile = { name = var.profile }
}

variable "profile" { type = string }
variable "project_id" { type = string }
variable "subnet_id" { type = string }
variable "cpu_image_id" { type = string }
variable "cpu_platform" { type = string }
variable "cpu_preset" { type = string }
variable "security_group_id" { type = string }
variable "cloud_init_user_data" {
  type      = string
  default   = ""
  sensitive = true
}
variable "gpu_workers" {
  description = "Only admitted, cost-reviewed RTX/Cosmos worker configurations; empty creates no GPU workers."
  type        = map(object({ image_id = string, platform = string, preset = string }))
  default     = {}
}

resource "nebius_compute_v1_instance" "core" {
  parent_id = var.project_id
  name      = "preact-core"
  resources = { platform = var.cpu_platform, preset = var.cpu_preset }
  boot_disk = {
    attach_mode = "READ_WRITE"
    managed_disk = {
      name = "preact-core-boot"
      spec = {
        type            = "NETWORK_SSD"
        size_gibibytes  = 64
        source_image_id = var.cpu_image_id
      }
    }
  }
  network_interfaces = [{
    name              = "eth0", subnet_id = var.subnet_id, ip_address = {},
    public_ip_address = {}, security_groups = [{ id = var.security_group_id }]
  }]
  cloud_init_user_data = var.cloud_init_user_data
  recovery_policy      = "RECOVER"
}

resource "nebius_compute_v1_instance" "gpu" {
  for_each  = var.gpu_workers
  parent_id = var.project_id
  name      = "preact-${each.key}"
  resources = { platform = each.value.platform, preset = each.value.preset }
  boot_disk = {
    attach_mode = "READ_WRITE"
    managed_disk = {
      name = "preact-${each.key}-boot"
      spec = {
        type = "NETWORK_SSD", size_gibibytes = 250, source_image_id = each.value.image_id
      }
    }
  }
  network_interfaces = [{
    name            = "eth0", subnet_id = var.subnet_id, ip_address = {},
    security_groups = [{ id = var.security_group_id }]
  }]
  recovery_policy = "FAIL"
}

output "core_id" { value = nebius_compute_v1_instance.core.id }
output "gpu_ids" { value = { for key, worker in nebius_compute_v1_instance.gpu : key => worker.id } }
