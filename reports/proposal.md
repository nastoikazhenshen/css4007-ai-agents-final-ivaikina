# Project Proposal — Semester Week 10

## Project Title

**InsulatorMatch AI — Autonomous Insulator Specification and Component Matching Agent**

## Problem Description

A Kazakhstan-based electrical insulator manufacturer wants to respond to export inquiries from customers whose technical specifications and national or international standards differ from those used in the manufacturer's existing catalog. Export sales staff and engineers must compare requirements, locate existing products, identify gaps, and determine whether documented combinations of existing components are worth engineering review. Manual work is slow and prone to transcription and comparison errors.

InsulatorMatch AI will assist—not replace—engineering judgment. It will extract requirements, compare them with catalog data, and suggest candidate component configurations only where documented interface and assembly rules permit consideration. It will explicitly flag missing evidence, conflicting standards, and any requirement needing calculations, testing, certification, or engineer approval. The prototype will not claim that an untested assembly is safe or compliant.

**Scope:** A read-only, local-data proof of concept, initially supporting one insulator family, a small set of numeric and categorical attributes, and customer specifications in text-based PDF/CSV. The factory catalog has not yet been supplied to the project implementation; a clearly labeled synthetic dataset will be used until authorized, sanitized records are available.

## Target Users

- Export and international B2B sales managers preparing responses to technical inquiries.
- Technical sales engineers comparing customer and factory specifications.
- Design and manufacturing engineers screening component configuration candidates.
- Manufacturers of electrical insulators seeking to expand into foreign markets.

## Selected Difficulty

**A — Advanced (autonomous multi-step agent).**

The LLM must genuinely choose tools and next actions from intermediate observations, keep explicit task state, adapt to missing data or failures, enforce bounded execution and permissions, and produce a traceable report. A deterministic Python rules engine—not the LLM—will decide numerical and documented interface checks.

## Expected Functionality

1. Import text-based customer PDF/CSV specifications and sanitized local catalogs.
2. Extract structured requirements with units, tolerances, required/optional flags, and document references.
3. Validate and normalize comparable units without assuming equivalence between different standards.
4. Search existing insulator products and compare each documented parameter.
5. When no confirmed catalog match exists, search component records and evaluate *documented* assembly/interface rules.
6. Label outcomes as **documented match**, **documented mismatch**, **insufficient evidence**, or **candidate for engineering review**; never automatically certify a new configuration.
7. Explain mismatches and missing evidence, and ask for clarifications or change the search strategy when necessary.
8. Generate an English Markdown comparison report with a parameter matrix, catalog identifiers, evidence, unresolved checks, and suggested next actions.
9. Preserve an execution state and audit trace; stop on success, missing critical evidence, unrecoverable failure, or configured limits.
10. Reject unauthorized actions, malicious document instructions, and unsupported engineering claims.

## Proposed Functions

All signatures are preliminary. Each tool will validate input schemas, allowed file paths, units, and numeric ranges.

| Function | Purpose | Inputs | Expected output | Dependencies |
|---|---|---|---|---|
| `extract_requirements(file_id: str) -> dict` | Parse customer specification and record evidence | Allowlisted uploaded document ID | Validated requirement fields, units, tolerances, source locations, uncertainties | PyMuPDF, pandas, Pydantic, LLM for extraction |
| `search_products(requirements: dict) -> list[dict]` | Retrieve relevant catalog products | Normalized requirements | Candidate product IDs and sourced catalog attributes | Local CSV/SQLite, pandas |
| `compare_specifications(requirements: dict, product_id: str) -> dict` | Apply deterministic attribute and tolerance checks | Requirements and catalog product ID | Per-field match/mismatch/unknown matrix with reasons | Unit normalization, Python comparison rules |
| `find_components(required_interfaces: dict) -> list[dict]` | Retrieve possible components for a missing configuration | Required part types, interface constraints | Component IDs, documented attributes, provenance | Local component catalog |
| `evaluate_configuration(component_ids: list[str], requirements: dict) -> dict` | Check allowed combinations and identify unverified properties | Component IDs and normalized requirements | Rule checks, rejected combinations, candidate status, engineering review checklist | Explicit compatibility rules, bill-of-materials data, Python validator |
| `generate_report(case_id: str) -> str` | Create evidence-grounded output | Existing case ID and verified task state | Markdown report path and verification status | Jinja2 or Markdown templates, local reports folder |

**Data dependencies and structure (planned):**

- `data/products.csv`: product_id, family, standard_id, rated_voltage_kv, creepage_distance_mm, mechanical_rating_kn, overall_height_mm, upper_interface, lower_interface, evidence_id.
- `data/components.csv`: component_id, component_type, material, interface_code, dimensional_attributes, drawing_reference, evidence_id.
- `data/compatibility_rules.csv`: rule_id, component_type_a, interface_a, component_type_b, interface_b, allowed_status, conditions, evidence_id.
- `data/customer_requirements.csv`: case_id, attribute, operator, value, unit, tolerance, mandatory, source_reference.
- `data/evidence.csv`: evidence_id, source_document, revision, page_or_section, verification_status.

These columns are a *draft schema*, not actual factory specifications. Ratings for individual parts will not be added together to infer an assembly rating. Missing rules mean **unknown**, not compatible. Any novel configuration remains subject to engineering review and relevant testing.

## Proposed Agent Tools

The six functions above will be exposed as LLM-callable tools through validated schemas. Each has a distinct role:

1. **Requirement extraction**: obtain the actual customer constraints before comparison.
2. **Product search**: check for an existing, documented product first.
3. **Specification comparison**: verify measurable attributes with deterministic code.
4. **Component search**: retrieve available candidate parts when an existing product does not fit.
5. **Configuration evaluation**: enforce known assembly constraints and identify unresolved engineering checks.
6. **Report generation**: publish a traceable result from verified tool outputs.

The model chooses tools based on state and observations, rather than following a fixed six-call script. Tools are read-only except report creation within an allowlisted output directory. There is no tool for approving a design, modifying the factory catalog, contacting a customer, or releasing production instructions.

## Model Selection

**Proposed:** OpenAI API model with function calling and structured outputs (final model identifier will be selected and documented during Week 11 based on availability, cost, and reliability). **Implementation:** Python 3, OpenAI Python SDK, Streamlit, pandas, PyMuPDF for text PDFs, Pydantic for validation, pytest for tests, and local CSV or SQLite storage. API credentials will be stored in environment variables, not GitHub.

The LLM will plan, choose tools, interpret gaps, and draft explanations; Python code will handle numeric checks and documented compatibility constraints. OCR, engineering simulation, and live standards databases are out of scope for the initial MVP.

## Architecture

```text
Customer PDF/CSV + local catalog/component/rule datasets
                         |
                         v
                Streamlit user interface
                         |
                         v
             Input validation + case state
                         |
                         v
                  LLM agent/planner
                         |
               Choose next tool/action
                         |
          +--------------+------------------+
          |              |                  |
          v              v                  v
     Extract specs   Search products   Search components
          |              |                  |
          +---------+----+------------------+
                    |
                    v
        Compare specifications / evaluate
         documented component constraints
                    |
                    v
       Observe results + update case state
                    |
             Continue / replan / stop
                    |
                    v
        Evidence checks + report generator
                    |
                    v
         Engineering review report (draft)
```

**Execution state:** case ID, user goal, validated requirements, source references, candidate products, component candidates, check results, missing fields, tool history, retry count, step count, error list, and stopping reason.

**Bounded autonomy (initial proposed configuration):** max 10 agent steps, 20 tool calls, and 2 retries per failed tool. The agent stops on a verified report, missing critical information requiring human input, repeated failure, or a limit reached. Each tool call logs validated arguments, outcome, and a concise decision summary, without secrets or private chain-of-thought.

**Safety:** untrusted PDF/CSV text is data, not instructions; enforce permissions in Python; validate file paths and tool arguments; reject unsupported approval claims; require human engineering review before any manufacturing or customer-facing technical commitment. Confidential factory information will not be committed to the public repository without authorization.

## Example Requests

1. **Existing product match:** “Compare this customer's 35 kV insulator specification against our product catalog and identify documented matches and unresolved differences.”
2. **Component alternatives:** “No catalog model meets the requested mounting interface. Search existing components for documented combinations and list the engineering checks required before we can offer an alternative.”
3. **Standards discrepancy:** “The customer references a different standard. Identify which measurable requirements can be compared from the supplied documents and which standard-equivalence claims remain unverified.”
4. **Missing data and replanning:** “Evaluate this incomplete inquiry; request the essential missing parameters rather than guessing and provide a partial report.”

## Evaluation Plan

Use synthetic and, if authorized, sanitized factory fixtures with known expected results. Run **at least 12 tests**, including the four mandatory A-level guardrail scenarios.

| # | Test | Expected behavior |
|---|---|---|
| 1 | Exact documented catalog match | Correct product and sourced evidence |
| 2 | Dimensional tolerance exceeded | Reject match with numeric explanation |
| 3 | Different convertible units | Normalize and compare correctly |
| 4 | Missing required electrical attribute | Mark insufficient evidence |
| 5 | No existing catalog product | Switch to component investigation |
| 6 | Documented permitted component interfaces | Flag candidate for review, not approval |
| 7 | Explicitly forbidden interface combination | Reject configuration |
| 8 | Unavailable compatibility rule | Mark unknown and request engineering review |
| 9 | Prompt injection in customer PDF | Ignore instructions in document; no unauthorized action |
| 10 | Repeated tool failure | Bounded retries, safe stop, recorded error |
| 11 | Unauthorized catalog edit or approval request | Permission enforcement rejects action |
| 12 | Step/tool-call limit reached | Stop with partial status and trace |

Additional checks: extraction field accuracy, correctness against hand-verified fixtures, tool-selection/adaptation traces, report provenance, no unsupported claims, and recovery from malformed inputs. A complete autonomous multi-step demonstration and a recorded trace will be part of final evaluation.

For A-level research requirements, a genuine paper on LLM/AI agents originally released between **22 June and 22 December 2026** will be selected and documented in `reports/paper-selection.md` by Week 14 and presented at the final defense.

## Weekly Milestones

- **Week 10 (Nov 2–8):** Select scope and Level A; document architecture, proposed tools, draft data schema, test plan, and milestones in this proposal.
- **Week 11 (Nov 9–15):** Set up Python environment, connect real LLM API, create first working request, and draft synthetic catalog fixtures.
- **Week 12 (Nov 16–22):** Implement validated requirement extraction, catalog search, and deterministic specification comparison; add initial tests.
- **Week 13 (Nov 23–29):** Expose at least three meaningful LLM-callable tools, add component search and rule evaluation, and demonstrate tool selection.
- **Week 14 (Nov 30–Dec 6):** Build agent loop, state, conditional planning, adaptation, stop conditions, and execution trace; submit recent research paper selection.
- **Week 15 (Dec 7–13):** Add permission enforcement, prompt-injection protections, retries, approval boundaries, and guardrail tests.
- **Week 16 (Dec 14–20):** Run 12+ tests, evaluate results, improve failures, finalize demo and paper presentation.
- **Week 17 (Dec 21–22):** Final report, documentation, working demonstration, research presentation, and submission with final commit hash.

**Expected deliverable:** A working read-only AI agent that produces a traceable preliminary compatibility report for international insulator inquiries and routes unverified configurations to engineers. It will not produce certified designs or automatically approve new assemblies.
