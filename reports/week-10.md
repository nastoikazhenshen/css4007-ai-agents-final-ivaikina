# Semester Week 10 Progress Report

> **Reporting period:** 2–8 November 2026. **Original Week 10 documentation committed:** 9 November 2026. **Scope revision recorded:** 14 November 2026. This report distinguishes initial planning from the later revised proposal; no work is backdated.

## Completed Work

- Created the required fork of the instructor's CSS4007 final-project repository and reviewed the project calendar, level-specific guidance and mandatory Week 10 proposal sections.
- Initially proposed a technical insulator-specification matching agent and selected **A — Advanced** as the target difficulty. This initial proposal was written and committed on 9 November, after the official Week 10 reporting window.
- Prepared an initial problem definition, prospective users, preliminary tool descriptions, architectural workflow, evaluation plan and Weeks 10–17 milestones. This was proposal work only, not a functioning LLM application.
- **Later scope revision (14 November):** Refocused the project on **TenderScout AI**, a tender-research agent for construction/reconstruction/modernization of electric power lines. Replaced the proposal's functions, tool plan, architecture and evaluation scenarios accordingly.
- The revised target covers official procurement notices from Indonesia, Thailand, Turkey, the United States, Mexico, Brazil, Argentina and Peru, without a minimum voltage requirement or mandatory insulator mentions. It prioritizes country, buyer, project, verified procurement contact/email, direct official tender link and actionable deadline; technical specifications are optional.

The revised scope belongs to the later planning update and is **not represented as development completed during 2–8 November**.

## Important Commits

- **9 November — initial proposal:** `e6d8452cb54af80016cc4762b2258556d04090a5` — documented the original specification-matching proposal. https://github.com/nastoikazhenshen/css4007-ai-agents-final-ivaikina/commit/e6d8452cb54af80016cc4762b2258556d04090a5
- **9 November — Week 10 report:** `027e85398d4df5e3dcb599108fe2d8ec0c5f14e6` — recorded initial proposal progress and its real reporting date. https://github.com/nastoikazhenshen/css4007-ai-agents-final-ivaikina/commit/027e85398d4df5e3dcb599108fe2d8ec0c5f14e6
- **14 November — scope revision:** This report and the revised proposal are committed as a later project change. The revision commit is visible in Git history; it is not evidence of a Week 10 code contribution.

Inherited instructor commits and report-only updates do not establish meaningful Week 10 application development. The course instructor determines activity credit.

## Current Functionality

In the **university fork**, the current deliverable at this reporting point is a revised design/proposal. No working OpenRouter call, LLM-selected tool loop, autonomous investigation, production eight-country collection or Google Sheets synchronization is claimed for Week 10.

Separate development work on official-source Python collectors and JSON contracts may inform the future implementation, but those components are not represented here as already integrated or demonstrated in this fork.

## Problems Encountered

- The original specification-matching project relied on engineering catalog data that was not available for a defensible prototype.
- Public tender information is heterogeneous across languages, organizations and data formats; some portals restrict automated access.
- A relevant power-line project may not mention individual materials, and its contact or submission deadline may be located in an attachment.
- An LLM could incorrectly present an archived, uncertain or unverified notice as a currently actionable opportunity.
- The scope change occurred after the Week 10 window and needed to be documented without altering actual dates.

## Solutions

- Revised the proposal toward a narrower, testable autonomous research workflow based on **official-source evidence** and clearly defined output fields.
- Proposed independent country connectors with a common collector schema and explicitly recorded collection failures/completeness.
- Planned LLM-selectable investigation tools plus deterministic deadline checks, output validation, and evidence-based routing to Active, Pending, History or Excluded.
- Defined bounded multi-step execution, tool permissions, prompt-injection defenses, trace logging, and human approval where necessary.
- Recorded the project change as a **14 November revision**, preserving the original 9 November proposal and report commits.

## What I Learned

- A target **A level** requires a real autonomous sequence with at least three meaningful LLM-selected tools, explicit state, adaptation, retries and stopping conditions; a flowchart or a list of Python functions is not enough.
- A reliable agent must distinguish source retrieval, LLM-assisted interpretation and independent validation of source-backed facts.
- Schema-valid JSON alone does not prove a tender is active or that an email was actually published by the buyer.
- Honest weekly reporting should preserve real contribution dates and distinguish planned features from implemented behavior.

## Next Week

- During Week 11 (9–15 November), implement and document a **real OpenRouter model API call** in the required university fork, recording the actual response/error and selected model.
- Set up a reproducible Python environment and a minimal provider client configured through environment variables, with secrets excluded from Git.
- Use a small public or mocked tender example to demonstrate the first LLM-powered classification/research response; begin input/output validation.
- Add a meaningful code commit and document actual Week 11 evidence in `reports/week-11.md`; do not report a successful model call until it has been executed.
