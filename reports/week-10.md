# Semester Week 10 Progress Report

## Completed Work

- Selected **InsulatorMatch AI**, an autonomous technical specification and component matching agent for international electrical-insulator export inquiries.
- Selected **Level A — Advanced** and defined a bounded, read-only decision-support scope.
- Prepared `reports/proposal.md` with problem statement, target users, six preliminary LLM-callable tools, architecture, model approach, data schema, example requests, evaluation plan, and Weeks 10–17 milestones.
- Identified engineering safety constraints: no automatic certification, no undocumented compatibility claims, and mandatory human review of novel component configurations.
- Planned a synthetic fixture dataset pending authorization to use sanitized manufacturer catalog records.

This is planning/documentation progress only. No working LLM integration, production catalog import, or functional test results are claimed for Week 10. This report was documented on November 9, 2026, after the November 2–8 Week 10 window.

## Important Commits

- Commit hash: `e6d8452cb54af80016cc4762b2258556d04090a5`
- Description: Completed the Week 10 Level A InsulatorMatch AI proposal.
- Link: https://github.com/nastoikazhenshen/css4007-ai-agents-final-ivaikina/commit/e6d8452cb54af80016cc4762b2258556d04090a5

## Current Functionality

The application is in the proposal/design stage. Planned capabilities are specification extraction, catalog matching, deterministic comparison, component search, compatibility-rule evaluation, and evidence-based report generation. These are not implemented yet.

## Problems Encountered

- Customer standards may differ from the factory's documented specifications.
- A product catalog may describe complete insulators without sufficient information about individual components and approved assemblies.
- Actual factory engineering data has not yet been provided for implementation.
- Electrical and mechanical ratings of a proposed assembly cannot safely be inferred by simply combining individual component ratings.

## Solutions

- Defined separate datasets for products, components, compatibility rules, customer requirements, and evidence references.
- Proposed synthetic demonstration fixtures until permitted, sanitized factory records are available.
- Assigned numeric and interface checks to deterministic Python functions, with the LLM responsible for planning and tool selection.
- Required the agent to label unsupported matches as unknown and to route candidate assemblies for engineering review.

## What I Learned

- An A-level agent needs genuine LLM-selected tools, multi-step stateful execution, adaptation, bounded retries, execution traces, and enforceable guardrails.
- Evidence provenance and explicit uncertainty are essential when comparing engineering specifications.
- A narrow and verifiable proof of concept is preferable to attempting to automate engineering certification.

## Next Week

- Set up the Python project and environment.
- Choose an available OpenAI API model and implement the first real LLM request.
- Create a small synthetic catalog and customer inquiry fixture.
- Implement basic input validation and document the first working prototype.
- Record actual Week 11 code changes, tests, and commits in `reports/week-11.md`.
