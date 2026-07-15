# HELIXos Live-Operation Directive

## Present-System Definition

HELIXos must operate through real computing environments in substantially the same way a competent human operator would operate at a terminal or web browser.

The present execution layer consists of **Digital Operators**:

* Terminal Operators
* Browser Operators
* Repository Operators
* Application Operators
* Research Operators

Physical robots, embodied devices, mechanical automation, and autonomous hardware are future-direction concepts and are outside the current implementation scope.

The terms `Robot Executor` and `Robot Agent` may remain as internal legacy labels, but all current specifications and user-facing documentation should use:

```text
Digital Operator
```

---

## 1. No Mock or Placeholder Content

Production, development, demonstration, and acceptance environments must not depend on fabricated operational behavior.

The following are prohibited as substitutes for functioning implementation:

* mock API responses;
* placeholder records;
* hardcoded success messages;
* fake terminal output;
* simulated browser actions;
* static dashboard values presented as live;
* buttons without implemented actions;
* empty integration adapters;
* fabricated audit events;
* placeholder authentication;
* fake model-evaluation results;
* synthetic completion states;
* "coming soon" controls inside accepted workflows;
* code paths that return success without performing the requested operation.

Synthetic data may be used only in isolated automated tests when clearly labeled as test fixtures. It may not be used to demonstrate that a live integration works.

Every visible operational result must be traceable to:

1. an actual system action;
2. an actual external or local resource;
3. an actual tool result;
4. an actual state transition;
5. a verifiable audit event.

---

## 2. Human-Equivalent Computer Operation

Digital Operators must be able to work through the same practical interfaces available to a human at a workstation.

### 2.1 Terminal Operation

A Terminal Operator must be capable of:

* opening an authorized shell;
* identifying the current user, host, directory, and environment;
* navigating filesystems;
* reading and editing permitted files;
* running approved commands;
* inspecting standard output and standard error;
* checking process exit codes;
* starting and stopping authorized services;
* reading application and system logs;
* using Git and repository tools;
* installing approved dependencies;
* running build, lint, test, and validation commands;
* interacting with package managers;
* handling interactive command prompts;
* detecting hung processes;
* preserving execution transcripts;
* stopping when privilege escalation or human authorization is required.

The Operator must interpret command results rather than assuming success because a command was issued.

A command returning a nonzero exit code, malformed output, missing artifact, failed test, or inaccessible resource must be treated as a real failure.

### 2.2 Browser Operation

A Browser Operator must be capable of:

* opening real websites and web applications;
* navigating using links, menus, tabs, and browser controls;
* reading rendered pages;
* inspecting page structure when permitted;
* entering data into forms;
* selecting menus, dropdowns, checkboxes, and controls;
* uploading and downloading files;
* handling multi-step workflows;
* managing ordinary authentication sessions;
* pausing for passwords, passkeys, CAPTCHA, or MFA when human participation is required;
* reading notifications and validation errors;
* verifying that submitted changes actually persisted;
* taking screenshots for evidence;
* checking browser console and network failures when relevant;
* comparing visible state before and after an action;
* recovering from redirects, expired sessions, and unexpected page states.

The Operator must support both:

```text
DOM-guided interaction
```

and:

```text
visual interface interaction
```

A workflow is not accepted merely because an underlying HTTP request succeeded. The final rendered state must also be verified when the task concerns a user-facing application.

---

## 3. Real Integrations Only

HELIXos adapters must connect to actual supported systems.

Examples include:

* real Git repositories;
* real local filesystems;
* real development servers;
* real browser sessions;
* real Obsidian vault files;
* real model gateways;
* real databases;
* real APIs;
* real test runners;
* real deployment providers;
* real authorized online services.

Each integration must expose a health check that proves:

* credentials or sessions are valid;
* the target resource exists;
* required permissions are present;
* read operations work;
* authorized write operations work;
* failures are surfaced accurately;
* audit events record the real result.

An adapter that only defines an interface without successfully connecting to its target is classified as:

```text
NOT IMPLEMENTED
```

It must not be shown as operational.

---

## 4. Digital Operator Execution Model

A Digital Operator does not receive only an abstract objective and improvise without limits.

Each operation must resolve to a complete execution package containing:

* task ID;
* approved objective;
* target environment;
* target application or repository;
* permitted accounts;
* permitted files, URLs, and domains;
* allowed tools;
* prohibited actions;
* expected result;
* verification method;
* timeout and loop limits;
* rollback or recovery instructions;
* approval state;
* audit reference.

The compact Triangulated Instruction remains a routing representation:

```text
[TinyPointer | Action Unicode | Digest Tag]
```

It must resolve to the complete authorized package before terminal or browser interaction begins.

---

## 5. Observe–Act–Verify Loop

Every Digital Operator must follow this cycle:

```text
OBSERVE
→ INTERPRET
→ ACT
→ VERIFY
→ RECORD
```

### Observe

Capture the real current state before acting.

Examples:

* current page;
* active branch;
* existing files;
* running process;
* database migration status;
* authenticated account;
* current form contents.

### Interpret

Determine whether the observed state matches the expected preconditions.

Do not act when:

* the wrong account is active;
* the wrong repository or branch is open;
* the target differs from the approved scope;
* required information is missing;
* the environment is unsafe;
* the action would exceed authorization.

### Act

Perform one bounded operation.

Avoid combining unrelated destructive or irreversible operations into one action.

### Verify

Confirm the actual effect.

Examples:

* file changed and contains the expected content;
* test genuinely passed;
* page shows the saved value;
* deployment URL loads;
* API returns the new record;
* commit exists on the expected branch;
* downloaded file is present and valid.

### Record

Append the actual action, result, evidence, and resulting state to the execution ledger.

---

## 6. Human Handoff Conditions

A Digital Operator must pause rather than imitate, bypass, or fabricate human input when encountering:

* MFA prompts;
* CAPTCHA;
* passkeys;
* biometric authentication;
* legal agreements;
* financial authorization;
* irreversible deletion;
* production deployment approval;
* credential creation or exposure;
* unclear identity or account context;
* permission escalation;
* security warnings;
* conflicting operator instructions;
* ambiguous high-impact forms;
* content requiring personal attestation;
* any action that legally or contractually requires a person.

The Tactical HUD must present:

* the current screen or terminal state;
* the requested human action;
* why it is required;
* the consequence of proceeding;
* safe options;
* the state from which execution will resume.

---

## 7. Live Acceptance Standard

A feature is complete only when it performs its intended workflow against a real authorized target.

### Browser feature acceptance

A browser workflow must demonstrate:

1. the correct site is opened;
2. the correct account or permitted anonymous context is active;
3. the workflow is completed through the actual interface;
4. submitted data persists;
5. the resulting state is visible after refresh or re-navigation;
6. errors and session failures are handled;
7. evidence is captured.

### Terminal feature acceptance

A terminal workflow must demonstrate:

1. the correct host and directory are selected;
2. the approved command is run;
3. output and exit status are captured;
4. resulting files or services are inspected;
5. tests or health checks validate the result;
6. failures do not produce false completion;
7. execution is recorded.

### Integration acceptance

An integration must demonstrate at least one real read operation and one explicitly authorized real write operation where the service supports writes.

Read-only integrations must demonstrate retrieval of real current information.

---

## 8. System Status Labels

HELIXos must distinguish these states precisely:

| Status             | Meaning                                                                    |
| ------------------ | -------------------------------------------------------------------------- |
| Specified          | Requirements exist; no implementation claim                                |
| Connected          | Real service connection established                                        |
| Read-Validated     | Real data successfully retrieved                                           |
| Write-Validated    | Authorized real change successfully persisted                              |
| Workflow-Validated | Complete real workflow passed                                              |
| Production-Ready   | Security, recovery, observability, and acceptance gates passed             |
| Blocked            | External permission, credential, infrastructure, or human action required  |
| Not Implemented    | Interface or design exists without working behavior                        |

"Demo," "prototype," or "beta" must not imply fake behavior. These terms may describe maturity, but all represented functions must still operate against real systems.

---

## 9. Current Executor Classification

```yaml
execution_layer:
  present:
    type: "digital_operator"
    interfaces:
      - "terminal"
      - "web_browser"
      - "repository"
      - "desktop_application"
      - "authorized_api"
    requirement: "real end-to-end operation"
  future:
    type: "physical_robotics"
    status: "out_of_current_scope"
```

Aider and OpenHands are classified as code-oriented Digital Operators.

Browser-use and Playwright-based workers are classified as Browser Digital Operators.

Future embodied robotics may reuse HELIXos planning, policy, provenance, and HITL systems, but physical actuation must not shape or delay the present software architecture.

---

## 10. Governing Principle

HELIXos must never confuse a representation of work with completed work.

A plan is not execution.

A tool call is not success.

A generated message is not evidence.

A dashboard status is not authoritative unless backed by real state.

A compact pointer is not an instruction until resolved and authorized.

A workflow is complete only when the requested effect occurred in the real target environment and that effect was independently verified.
