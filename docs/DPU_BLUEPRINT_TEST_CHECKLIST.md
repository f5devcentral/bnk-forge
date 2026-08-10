# DPU Blueprint Test Checklist

> End-to-end testing procedure for bare-metal DPU deployment blueprints.
> Run this checklist after any changes to the SSH engine, bare-metal modules,
> stack deployment, or related UI components.

## Prerequisites

- [ ] Local deploy running: `make local-deploy`
- [ ] Test server reachable: `ssh ubuntu@10.176.11.91` (password: F5@apcj)
- [ ] BFB image URL available (e.g., from content.mellanox.com)
- [ ] Browser open to https://localhost

## 1. Fresh Stack Deploy (Happy Path)

### 1.1 Create project
- [ ] Projects → Create Project
- [ ] Name: "dpu-test-1"
- [ ] Type: "Bare Metal DPU"
- [ ] SSH Connection: create inline OR select existing (ubuntu@10.176.11.91)
- [ ] Project created successfully

### 1.2 Register bare-metal host
- [ ] Project → Bare Metal tab
- [ ] Click "Add Host"
- [ ] Host IP: 10.176.11.91 (or .143/.214/.226 depending on target)
- [ ] SSH credential: select or create
- [ ] Host registered, shows in list

### 1.3 Deploy blueprint
- [ ] Project → Stacks tab → Deploy Blueprint
- [ ] Select "BNK DPU Infrastructure"
- [ ] **Verify:** `bfb_url` appears as required field with red asterisk
- [ ] Fill in BFB URL
- [ ] Fill in cluster_name
- [ ] Click Deploy
- [ ] **Verify:** Stack created, shows "deploying" status with progress

### 1.4 Module execution — step by step
- [ ] **probe-dpu**: Init starts automatically → Run completes
  - [ ] Status updates live (3s polling)
  - [ ] Logs tab shows [probe] lines
  - [ ] Outputs: 22 fields including nic_mode, host_os, k8s_installed
  - [ ] nic_mode shows "dpu" or "nic" (not "unknown")

- [ ] **set-nic-mode** (optional): Runs if needed
  - [ ] Uses sudo for mlxconfig
  - [ ] Shows connectivity-risk badge if applicable

- [ ] **flash-dpu**:
  - [ ] Connectivity-risk badge visible (⚠ SSH may drop)
  - [ ] Pre-stage: VF netplan staged
  - [ ] BFB download + flash via bfb-install
  - [ ] Reconnect wait (up to 600s)
  - [ ] Validate: DPU reachable after flash

- [ ] Remaining modules execute in dependency order

### 1.5 Stack completion
- [ ] Stack status transitions to "deployed" when all modules applied
- [ ] No modules stuck in transitional state

## 2. Single Module Operations

### 2.1 Init → Run individual module
- [ ] Click module → ellipsis menu → Init
- [ ] Status changes to "initializing" → "initialized"
- [ ] Click "Run" button
- [ ] Status changes to "applying" → "applied"
- [ ] Logs visible in detail panel
- [ ] Stack status stays "pending" (not "deploying")

### 2.2 Re-run
- [ ] Module in "applied" state → Re-run button visible
- [ ] Click Re-run → module resets → Init + Run automatically
- [ ] New logs appear in Logs tab

### 2.3 Input validation
- [ ] Try to Init flash-dpu without bfb_url set
- [ ] **Expected:** Error: "Missing required inputs: bfb_url"
- [ ] Module stays in current state (not stuck)

## 3. Error Recovery

### 3.1 Module failure recovery
- [ ] If a module fails (apply_failed), Re-run button appears
- [ ] Re-run resets and retries
- [ ] Stack deploy can be retried (modules in failed state get re-queued)

### 3.2 Stack stuck in deploying
- [ ] If stack shows "deploying" but no modules are active:
  - [ ] Clicking Deploy again should reconcile status to "pending" first
  - [ ] Delete should reconcile and proceed

### 3.3 Module stuck in applying
- [ ] If module shows "applying" but task completed/failed:
  - [ ] Stack progress update should detect and recover
  - [ ] Module status corrected to applied/failed based on task status

## 4. UI Checks

- [ ] SSH modules: "Run" button (not "Apply"), no Plan option
- [ ] SSH modules: Confirmation dialog says "Run Module" (not "Apply Infrastructure")
- [ ] Connectivity-risk badge on flash-dpu: ⚠ icon + "SSH may drop" pill
- [ ] Stage detail shows live progress (e.g., "[probe] Checking MST devices...")
- [ ] Logs tab: inline task logs with correct labels (Run/Init)
- [ ] Dates: valid format (no "Invalid Date")
- [ ] Toast notifications: "Run operation started" (not "Apply")
- [ ] No "Recover State" or "Destroy" in SSH module menu
- [ ] Required inputs: bfb_url shown with red asterisk in deploy dialog

## 5. Regression Checks

- [ ] OpenTofu modules still work (Plan/Apply/Destroy flow unchanged)
- [ ] K8s modules still work
- [ ] `make pre-push` passes (lint + all test suites)
