# Nebius Compute Inputs

Provider pinned to downloaded 0.6.64; Terraform 1.13.5. Actual provider-schema validation passes.
Version 0.6.57 was listed in the registry but its release assets returned 404. Current official resource
schema: https://docs.nebius.com/terraform-provider/reference/resources/compute_v1_instance.
Select admitted project/subnet/images/platforms/presets/security group from the account;
no guessed image IDs or paid resource provisioning is part of tests.

Set variables in an uncommitted tfvars file. Configure a scoped Nebius service-account
CLI profile. `terraform init`, `terraform validate` and a reviewed `terraform plan` precede
any apply. Empty gpu_workers creates no GPU workers. Isaac requires an admitted RTX platform;
Cosmos's GPU/memory choice requires measured checkpoint feasibility. GPU VMs remain private.
Core's public interface depends on the provided security group; expose only the reviewed
TLS judge workflow. cloud_init_user_data must not place long-lived secrets in state.

This provisions compute boundaries; it does not claim installed worker dependencies or
completed application deployment. Use deploy/compose.yml for app/database, configure private
worker endpoints and artifact storage, and validate external judge access before release.
