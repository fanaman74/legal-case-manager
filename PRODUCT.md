# Case File Manager

<!-- impeccable:product-schema 1 -->

## Platform
web

## Stack
React and TypeScript with a FastAPI service, following the existing repository plan. SQLite and local files hold case material.

## Users
People organising legal case documents and reviewing the same evidence from different topic perspectives.

## Product Purpose
Upload case documents, give specialist agents distinct review topics, and inspect their findings with direct links to the original evidence.

## Capabilities and Constraints
Keep uploaded documents local. External AI is optional and requires explicit approval for each analysis. Results must identify source passages. Distinguish evidence retrieval from model-generated analysis. The original Windows launcher and multi-user deployment are planned capabilities, not present in the documentation-only starting commit.

The user requested a Model management section with Claude, ChatGPT, DeepSeek and OpenRouter selection. Keep provider keys in secure server settings; model and provider choices may be persisted locally.

## Brand Commitments
Use the Case File Manager identity. The user explicitly requested an interface similar to TencentCloud/Octop and supplied its desktop chat screenshot: light surfaces, a restrained rose accent, left navigation, a welcoming chat workspace, quick starts, and a generous composer.

## Evidence on Hand
The starting repository contained README.md and .gitignore only. See docs/original-project-plan.md. No real case files were supplied; do not invent case content or completed reviews.

## Product Principles
- Show the source behind each finding.
- Separate facts, candidate evidence, and generated interpretation.
- Ask before sending document excerpts to an external provider.
- Keep focused agents and their task results visible and reusable.
